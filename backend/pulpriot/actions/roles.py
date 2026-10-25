"""Stakeholders, role gates, approval ladder and auto-escalation.

Roles and escalation parents come from the workbook (M13_dim_stakeholder). Two things are not in the case and are ASSUMED:
the value limits that decide whether a planner, the supply-chain head or the COO approves (the workbook names a limit
"PAR100" but gives no figure here), and the response time before a pending item escalates (24 hours per level)."""
from __future__ import annotations

import pandas as pd

from ..data.loader import Dataset

# ASSUMED approval ladder in INR
LIMIT_PLANNER = 10_00_000          # 10 lakh
LIMIT_SCH = 1_00_00_000            # 1 crore


def stakeholders(ds: Dataset) -> list[dict]:
    d = ds.dims["stakeholder"]
    return [{"role_id": r["role_id"], "role": r["role"], "function": r["function"], "approves": r["approves"],
             "escalates_to": None if (pd.isna(r["escalates_to"]) or str(r["escalates_to"]).strip() in ("—", "-", "")) else r["escalates_to"]}
            for _, r in d.iterrows()]


def chain(ds: Dataset, role: str) -> list[str]:
    parent = {s["role_id"]: s["escalates_to"] for s in stakeholders(ds)}
    out, cur = [role], role
    while parent.get(cur) and parent[cur] not in out:
        cur = parent[cur]
        out.append(cur)
    return out


def can_decide(ds: Dataset, actor: str, approver: str) -> bool:
    """The named approver, or anyone above them in the escalation chain."""
    return actor in chain(ds, approver)


def approver_for(kind: str, cost: float | None) -> str:
    c = abs(cost or 0.0)
    if kind in ("copacker", "air_freight", "pulp_buffer"):
        if kind != "copacker":
            return "ROLE_PRC" if c <= LIMIT_SCH else "ROLE_COO"
        return "ROLE_SCH" if c <= LIMIT_SCH else "ROLE_COO"
    if kind in ("mix_shift", "pulp_tradeoff"):
        return "ROLE_COO"
    if kind in ("fefo_redirect", "discount"):
        return "ROLE_SAL"
    return "ROLE_PLN" if c <= LIMIT_PLANNER else "ROLE_SCH"


def escalation_state(ds: Dataset, action: dict, asof: str, sla_hours: float) -> dict:
    """Where a pending action sits at the as-of moment: SLA of 24 hours per level, then the next role up."""
    ch = chain(ds, action["approver_role"])
    created = pd.Timestamp(action["created_asof"])
    now = pd.Timestamp(asof)
    age_h = max((now - created).total_seconds() / 3600.0, 0.0)
    levels = int(age_h // sla_hours)
    idx = min(levels, len(ch) - 1)
    owner = ch[idx]
    next_in = None
    if idx < len(ch) - 1:
        next_in = sla_hours - (age_h - levels * sla_hours)
    overdue = bool(action.get("deadline") and pd.Timestamp(action["deadline"]) < now)
    return {"chain": ch, "current_owner": owner, "escalated": idx > 0, "escalates_to": ch[idx + 1] if idx < len(ch) - 1 else None,
            "hours_to_next_escalation": next_in, "age_hours": age_h, "deadline_passed": overdue, "sla_hours": sla_hours}
