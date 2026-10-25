"""M1 validation: duplicate keys, flavor-name aliases, bad dates, negative or impossible stock, unit slips, stale feeds.

Nothing is dropped silently. Every row that is rejected, remapped, flagged or quarantined lands in the audit log
with the table, the row reference, the rule that fired and the raw values."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..settings import Params

# Actions recorded in the audit log
REJECT, MAPPED, FLAG, QUARANTINE, STALE_FLAG = "REJECT", "MAPPED", "FLAG", "QUARANTINE", "STALE_DOWNGRADE"


@dataclass
class CleanContext:
    flavor_terms: dict[str, str]          # lower-cased alias -> flavor_id
    flavor_ids: set[str]
    depot_ids: set[str]
    depot_region: dict[str, str]
    depot_terms: dict[str, str]
    params: Params
    darkstore_prefix: dict[str, str] = field(default_factory=lambda: {"BLR": "DEP06"})

    @staticmethod
    def build(flavor: pd.DataFrame, depot: pd.DataFrame, synonym: pd.DataFrame, params: Params) -> "CleanContext":
        f_terms: dict[str, str] = {}
        d_terms: dict[str, str] = {}
        for _, r in synonym.iterrows():
            term = str(r["term"]).strip().lower()
            if r["entity_type"] == "flavor":
                f_terms[term] = str(r["entity_id"])
            elif r["entity_type"] == "depot":
                d_terms[term] = str(r["entity_id"])
        for _, r in flavor.iterrows():
            f_terms[str(r["flavor_id"]).lower()] = r["flavor_id"]
            f_terms[str(r["flavor_name"]).lower()] = r["flavor_id"]
            for a in str(r["aliases"]).split("|"):
                if a.strip():
                    f_terms[a.strip().lower()] = r["flavor_id"]
        ctx = CleanContext(f_terms, set(flavor["flavor_id"]), set(depot["depot_id"]),
                           dict(zip(depot["depot_id"], depot["region_id"])), d_terms, params)
        return ctx

    def map_flavor(self, raw) -> tuple[str | None, str | None]:
        """Return (flavor_id, method). method None means exact id."""
        if raw in self.flavor_ids:
            return raw, None
        key = re.sub(r"[\s_\-]+", " ", str(raw)).strip().lower()
        if key in self.flavor_terms:
            return self.flavor_terms[key], "ALIAS_OR_CASEFOLD"
        near = difflib.get_close_matches(key, list(self.flavor_terms), n=1, cutoff=0.82)
        if near:
            return self.flavor_terms[near[0]], f"FUZZY({near[0]})"
        return None, None


def parse_dates(s: pd.Series) -> pd.Series:
    """Strict ISO-style parse; anything else (31-02-2026, 2026/13/05, 2026-02-30) becomes NaT."""
    txt = s.astype(str).str.strip()
    ok = txt.str.match(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$")
    out = pd.to_datetime(txt.where(ok), errors="coerce", format="mixed")
    return out


def _audit(rows: list, table: str, idx, defect: str, action: str, reason: str, raw: dict) -> None:
    rows.append({"table": table, "row_ref": idx, "defect": defect, "action": action, "reason": reason, "raw": raw})


def _clean_generic(df: pd.DataFrame, table: str, ctx: CleanContext, *, date_col: str, arrival_col: str,
                   qty_col: str, dup_key: list[str], stale_days: float | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    audit: list[dict] = []
    d = df.copy()
    d["_row"] = np.arange(len(d))
    keep = pd.Series(True, index=d.index)
    flags = pd.Series("", index=d.index, dtype=object)

    # 1. dates
    d[date_col + "_raw"] = d[date_col]
    d[date_col] = parse_dates(d[date_col])
    d[arrival_col] = parse_dates(d[arrival_col])
    bad = d[date_col].isna() | d[arrival_col].isna()
    for i in d.index[bad]:
        _audit(audit, table, int(d.at[i, "_row"]), "bad_date", REJECT, f"invalid date '{d.at[i, date_col + '_raw']}'",
               {k: str(v) for k, v in df.loc[i].items()})
    keep &= ~bad

    # 2. flavor names
    fl = d["flavor_id"].map(lambda v: ctx.map_flavor(v))
    d["flavor_id"] = [a for a, _ in fl]
    for i, (_, m) in zip(d.index, fl):
        if m and keep[i]:
            _audit(audit, table, int(d.at[i, "_row"]), "bad_name", MAPPED,
                   f"flavor '{df.at[i, 'flavor_id']}' mapped to {d.at[i, 'flavor_id']} via {m}", {"flavor_id": df.at[i, "flavor_id"]})
    unknown_flavor = d["flavor_id"].isna()
    for i in d.index[unknown_flavor & keep]:
        _audit(audit, table, int(d.at[i, "_row"]), "unknown_id", REJECT, f"unknown flavor '{df.at[i, 'flavor_id']}'", {})
    keep &= ~unknown_flavor

    # 3. depot ids (dark-store codes map only when their region agrees with the depot's region)
    unknown_depot = ~d["depot_id"].isin(ctx.depot_ids)
    for i in d.index[unknown_depot & keep]:
        raw_id = str(d.at[i, "depot_id"])
        target = ctx.darkstore_prefix.get(raw_id.split("-")[0])
        region = d.at[i, "region_id"] if "region_id" in d.columns else None
        if target and (region is None or ctx.depot_region.get(target) == region):
            d.at[i, "depot_id"] = target
            unknown_depot[i] = False
            _audit(audit, table, int(d.at[i, "_row"]), "unknown_id", MAPPED, f"dark store '{raw_id}' mapped to {target}", {"depot_id": raw_id})
        else:
            why = "region disagrees with mapped depot" if target else "unknown depot"
            _audit(audit, table, int(d.at[i, "_row"]), "unknown_id", REJECT, f"{why}: '{raw_id}'", {"depot_id": raw_id})
    keep &= ~unknown_depot

    # 4. quantities: negative, then unit slips (lakh written as a small decimal)
    q = pd.to_numeric(d[qty_col], errors="coerce")
    neg = (q < 0) & keep
    unit = (q > 0) & (q < 100) & ((q - q.round()).abs() > 1e-9) & keep & ~neg   # bottle counts are whole numbers
    for i in d.index[neg]:
        if table == "T04_fact_depot_stock_weekly":
            _audit(audit, table, int(d.at[i, "_row"]), "negative", QUARANTINE, "negative stock; excluded, stock check requested", {qty_col: q[i]})
        else:
            _audit(audit, table, int(d.at[i, "_row"]), "negative", REJECT, "negative quantity", {qty_col: q[i]})
    for i in d.index[unit]:
        _audit(audit, table, int(d.at[i, "_row"]), "unit_error", FLAG,
               f"{qty_col}={q[i]} looks like lakh written as a decimal; excluded, not rescaled", {qty_col: q[i]})
    keep &= ~neg & ~unit

    # 5. duplicates (keep first)
    dup = d.duplicated(dup_key, keep="first") & keep
    for i in d.index[dup]:
        _audit(audit, table, int(d.at[i, "_row"]), "duplicate", REJECT, "duplicate key; first occurrence kept", {k: str(d.at[i, k]) for k in dup_key})
    keep &= ~dup

    # 6. stale feed: arrival long after the event. Row stays but is marked, and it only exists after its arrival.
    if stale_days is not None:
        lag = (d[arrival_col] - d[date_col]).dt.total_seconds() / 86400.0
        stale = (lag > stale_days + 1.0) & keep
        flags = flags.mask(stale, "STALE")
        for i in d.index[stale]:
            _audit(audit, table, int(d.at[i, "_row"]), "stale", STALE_FLAG,
                   f"arrived {lag[i]:.0f} days after the event; downgraded and usable only after arrival", {arrival_col: str(d.at[i, arrival_col])})

    out = d[keep].drop(columns=[date_col + "_raw"]).copy()
    out["dq_flag"] = flags[keep]
    out[qty_col] = pd.to_numeric(out[qty_col])
    return out.reset_index(drop=True), pd.DataFrame(audit, columns=["table", "row_ref", "defect", "action", "reason", "raw"])


def clean_qc(df: pd.DataFrame, ctx: CleanContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    return _clean_generic(df, "T02_fact_qc_daily", ctx, date_col="date", arrival_col="available_at_agent", qty_col="units_sold",
                          dup_key=["date", "flavor_id", "depot_id"], stale_days=ctx.params["stale_qc_days"])


def clean_stock(df: pd.DataFrame, ctx: CleanContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    return _clean_generic(df, "T04_fact_depot_stock_weekly", ctx, date_col="stock_date", arrival_col="report_received_at",
                          qty_col="qty_bottles", dup_key=["stock_date", "depot_id", "flavor_id", "lot_id"], stale_days=None)


def summarise_audit(audit: pd.DataFrame) -> list[dict]:
    if audit.empty:
        return []
    g = audit.groupby(["table", "defect", "action"]).size().reset_index(name="rows")
    return g.to_dict("records")
