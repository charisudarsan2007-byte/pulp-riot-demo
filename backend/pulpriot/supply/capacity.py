"""Production capacity options (E8), co-packer deadline clock (E9) and production mix reprioritiser (E10).

Plants are fixed in summer (about 2.5 crore bottles a month at 95% utilisation), so headroom is 5% in-house. The co-packer
adds up to 10% of in-house volume at +25% cost per bottle and needs 14 days notice. The reprioritiser only moves existing
line time between flavors; it never invents capacity."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.loader import FLAVORS
from ..engine.hierarchy import region_total_paths
from ..engine.pipeline import EngineRun
from ..inventory.mitigation import copacker_headroom
from ..inventory.risk import risk_grid
from . import pulp as PU


def capacity_options(run: EngineRun) -> dict:
    p = run.params
    plants = run.ds.dims["plant"]
    inhouse_cap = float(plants[plants.type == "in-house"]["capacity_bottles_month"].sum())
    pr = run.snap.production
    pr = pr[pr.plant_id.isin(["PLT1", "PLT2"])]
    wk = pr.groupby("week_start")["actual_bottles"].sum().sort_index().tail(4)
    month_rate = float(wk.mean() * 30.4 / 7) if len(wk) else 0.0
    util = month_rate / inhouse_cap if inhouse_cap > 0 else 0.0
    headroom = max(inhouse_cap - month_rate, 0.0)
    cp = copacker_headroom(run)
    unit_cost = p["price"] * (1 - p["margin"])
    return {
        "inhouse": {"capacity_month": inhouse_cap, "running_rate_month": month_rate, "utilisation": util, "headroom_month": headroom,
                    "headroom_share_case": p["inhouse_headroom_share"], "extra_cost_per_bottle": 0.0, "notice_days": 0,
                    "basis": "Last four reported production weeks (T09) against 2.5 crore bottles/month (CASE)."},
        "copacker": {"monthly_cap": cp["monthly_cap"], "available": cp["available"], "used_this_month": cp["used_this_month"],
                     "premium_pct": p["copacker_cost_premium"], "extra_cost_per_bottle": unit_cost * p["copacker_cost_premium"],
                     "notice_days": p["copacker_notice_days"], "basis": "Up to 10% of in-house volume, +25% per bottle, 14 days notice (CASE). Base cost DERIVED as price x (1 - margin)."},
        "line_rule": "Plants cannot add mid-season lines (CASE): in-house extra is utilisation headroom only.",
        "provenance": ["CASE", "SYNTHETIC", "CALCULATED"],
    }


def copacker_deadline(run: EngineRun, grid: list[dict] | None = None) -> dict:
    """Last date to place an extra order so it lands before the earliest-likely stockout (P10) of the most urgent cell."""
    p = run.params
    grid = grid or risk_grid(run)
    lanes = run.ds.dims["lane_plant"]
    transit = {d: int(g["transit_days"].min()) for d, g in lanes.groupby("depot_id")}
    rows = []
    for c in grid:
        if c["status"] == "GREEN":
            continue
        p10 = pd.Timestamp(c["date_p10"])
        order_by = p10 - pd.Timedelta(days=p["copacker_notice_days"] + transit.get(c["depot"], 3))
        rows.append({"flavor": c["flavor"], "depot": c["depot"], "p10_stockout": c["date_p10"], "order_by": str(order_by.date()),
                     "days_left": int((order_by - run.day).days), "status": c["status"]})
    rows.sort(key=lambda r: r["days_left"])
    head = rows[0] if rows else None
    return {"asof": str(run.day.date()), "notice_days": p["copacker_notice_days"], "headline": head, "rows": rows[:12],
            "expired": [r for r in rows if r["days_left"] < 0][:12],
            "rule": "order-by = earliest-likely (P10) stockout date - 14 days notice - co-packer to depot transit (lane ASSUMED from nearest plant).",
            "provenance": ["CASE", "ESTIMATED", "ASSUMED"]}


def reprioritiser(run: EngineRun, n: int = 300) -> dict:
    """Move existing weekly line time from comfortably covered flavors to urgent ones, within pulp availability."""
    p = run.params
    pr = run.snap.production
    pr = pr[pr.plant_id.isin(["PLT1", "PLT2"])]
    weeks = sorted(pr["week_start"].unique())[-4:]
    cur = pr[pr.week_start.isin(weeks)].groupby("flavor_id")["actual_bottles"].sum() / max(len(weeks), 1)
    grid = pd.DataFrame(risk_grid(run))
    cover = {}
    daily = {}
    for fl in FLAVORS:
        sub = grid[grid.flavor == fl]
        stock = float(sub["stock_p50"].sum())
        arr, _ = region_total_paths(run, fl, "ALL", 28, n)
        d = float(np.median(arr[:, :28].sum(axis=1)) / 28)
        daily[fl] = d
        cover[fl] = stock / d if d > 0 else 999.0
    target = 28.0
    urgent = {f: max((target - cover[f]) * daily[f], 0.0) / 4.0 for f in FLAVORS if cover[f] < 21}          # extra bottles per week to restore cover over ~4 weeks
    surplus = {f: min(max((cover[f] - p["overstock_cover_days"]) * daily[f], 0.0) / 4.0, 0.3 * float(cur.get(f, 0.0)))
               for f in FLAVORS if cover[f] > p["overstock_cover_days"]}
    inhouse = capacity_options(run)["inhouse"]
    freed = sum(surplus.values()) + inhouse["headroom_month"] * 7 / 30.4
    status = {s["ingredient"]: s for s in PU.pulp_status(run, 150)}
    moves, left = [], freed
    for f in sorted(urgent, key=lambda x: cover[x]):
        want = urgent[f]
        pulp_ok = True
        note = ""
        for r in PU.bom(run)[PU.bom(run).flavor_id == f].itertuples():
            st = status.get(r.ingredient_id)
            if st and st["critical"] and (st["runway_days"] or 0) < 7:
                pulp_ok = False
                note = f"{st['name']} runway {st['runway_days']:.0f} days: extra volume would be pulp-limited"
        give = min(want, left) if pulp_ok else 0.0
        left -= give
        moves.append({"flavor": f, "action": "INCREASE", "bottles_per_week": give, "wanted_per_week": want, "cover_days": cover[f],
                      "pulp_ok": pulp_ok, "note": note})
    take_total = sum(m["bottles_per_week"] for m in moves)
    pool = sum(surplus.values())
    scale = min(1.0, take_total / pool) if pool > 0 else 0.0
    for f, v in surplus.items():
        moves.append({"flavor": f, "action": "REDUCE", "bottles_per_week": v * scale, "wanted_per_week": v, "cover_days": cover[f], "pulp_ok": True,
                      "note": f"Holds {cover[f]:.0f} days of cover, above the {p['overstock_cover_days']}-day line"})
    return {"asof": str(run.day.date()), "current_weekly": {f: float(cur.get(f, 0.0)) for f in FLAVORS}, "cover_days": cover,
            "freed_capacity_per_week": freed, "moves": moves, "status": "PENDING (recommendation, not an action)",
            "rule": "Urgent = under 21 days of national cover. Source = flavors over 45 days of cover (max 30% of their line time) plus the 5% utilisation headroom.",
            "provenance": ["SYNTHETIC", "ESTIMATED"]}
