"""Safety stock, sellable-life clock and FEFO.

Safety stock replaces the flat 21-day rule with a target built from the lead-time + review-period demand PATHS:

    target   = Q_service( sum over lead time + review period of path demand )          (service level ASSUMED 95%)
    safety   = target - mean(protection-period demand)
    capped   = Q_service( GT part + min(MT/QC part, MT/QC demand over the sellable window) )   path by path

The cap reflects the shelf-life rule: MT and QC buyers reject stock with under 60 of 90 days left, so MT/QC stock held beyond
what can be sold in the remaining window only turns into discounts. The GT rule is unverified in the case, so no GT cap is applied.
The old 21-day rule is shown for comparison and is not presented as optimal."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..engine.common import summarise
from ..engine.forecast import get_demand_for_inventory
from ..engine.pipeline import EngineRun
from .position import Position, stock_position

SHELF = 90
MTQC_MIN = 60
MTQC_WINDOW = SHELF - MTQC_MIN        # days a fresh bottle stays sellable to MT/QC


def plant_lane(run: EngineRun, depot: str) -> dict:
    d = run.ds.dims["depot"].set_index("depot_id").loc[depot]
    lanes = run.ds.dims["lane_plant"]
    lane = lanes[(lanes.plant_id == d["primary_plant_id"]) & (lanes.depot_id == depot)]
    if lane.empty:
        lane = lanes[lanes.depot_id == depot].sort_values("transit_days").head(1)
    r = lane.iloc[0]
    return {"plant": r["plant_id"], "transit_days": int(r["transit_days"]), "freight_per_bottle": float(r["freight_per_bottle"])}


def old_rule_target(run: EngineRun, flavor: str, depot: str) -> dict:
    """The case's rule: about three weeks of cover on last year's mix. Uses the current month's allocation (T12)."""
    al = run.snap.allocation_history
    m0 = pd.Timestamp(year=run.day.year, month=run.day.month, day=1)
    r = al[(al.flavor_id == flavor) & (al.depot_id == depot) & (al.month == m0)]
    if r.empty:
        r = al[(al.flavor_id == flavor) & (al.depot_id == depot)].sort_values("month").tail(1)
    if r.empty:
        return {"target": None}
    monthly = float(r.iloc[0]["allocated_bottles"])
    days = r.iloc[0]["month"].days_in_month
    return {"target": monthly * run.params["old_cover_rule_days"] / days, "allocation_month": str(r.iloc[0]["month"].date()),
            "monthly_allocation": monthly, "basis": r.iloc[0].get("basis", "prior-year mix")}


def safety_stock(run: EngineRun, flavor: str, depot: str, service_level: float | None = None, n: int = 1000,
                 surge: float = 1.0, rho: float | None = None, pos: Position | None = None) -> dict:
    p = run.params
    sl = service_level or p["service_level"]
    lane = plant_lane(run, depot)
    lead = lane["transit_days"]
    review = int(p["review_days"])
    d = get_demand_for_inventory(run, flavor, depot, "ALL", lead, review, n=n, surge=surge, rho=rho)
    allp = d["paths"]
    horizon = lead + review
    pt = allp.sum(axis=1)
    target = float(np.quantile(pt, sl))
    mean_pt = float(pt.mean())
    # shelf-life cap, path by path
    by = d["by_channel"]
    window = max(MTQC_WINDOW - lane["transit_days"] - 1, 1)             # days the stock can still be sold to MT/QC after arrival
    mtqc = by["MT"] + by["QC"]
    from ..engine.hierarchy import build_paths
    longp = build_paths(run, flavor, depot, days=max(window, horizon), n=n, surge=surge, rho=rho)
    mtqc_window_total = (longp.by_channel["MT"] + longp.by_channel["QC"])[:, :window].sum(axis=1)
    cap_value = float(np.quantile(mtqc_window_total, 0.5))
    gt_pt = by["GT"].sum(axis=1)
    mtqc_pt = mtqc.sum(axis=1)
    capped_path = gt_pt + np.minimum(mtqc_pt, cap_value)
    target_capped = float(np.quantile(capped_path, sl))
    applied = target_capped < target - 1.0
    final = min(target, target_capped)
    old = old_rule_target(run, flavor, depot)
    pos = pos or stock_position(run, flavor, depot, 400)
    stock_now = float(np.median(pos.nowcast_paths)) if pos.nowcast_paths is not None else 0.0
    inbound = float(pos.inbound["qty_bottles"].sum()) if len(pos.inbound) else 0.0
    return {
        "service_level": sl, "service_level_source": p.sources.get("service_level", "ASSUMED") if hasattr(p, "sources") else "ASSUMED",
        "lead_time_days": lead, "review_days": review, "plant": lane["plant"],
        "lead_time_total": d["lead_time_total"], "protection_total": d["protection_total"],
        "target_uncapped": target, "target_final": final, "safety_stock": max(final - mean_pt, 0.0),
        "shelf_life_cap_applied": bool(applied), "mtqc_window_days": window, "mtqc_window_demand_p50": cap_value,
        "old_rule": old, "old_vs_new_ratio": (old["target"] / final) if old.get("target") and final > 0 else None,
        "stock_now_p50": stock_now, "inbound_confirmed": inbound,
        "gap_to_target": final - (stock_now + inbound), "interval_type": d["interval_type"],
        "formula": "target = Q_service(sum of path demand over lead time + review); safety stock = target - mean(protection demand)",
        "gt_rule_note": "GT shelf-life rule is unverified in the case; no GT cap applied.",
    }


# ---------------------------------------------------------------------------------------------------------
# lots, sellable-life clock, FEFO
# ---------------------------------------------------------------------------------------------------------
def lots_now(run: EngineRun, flavor: str, depot: str, pos: Position | None = None) -> pd.DataFrame:
    """Lot table as of today: last report plus receipts due since, with estimated outflow drawn by channel.

    Assumption (ESTIMATED): GT draws the oldest lots first; MT and QC draw the oldest lots that are still MT/QC-eligible."""
    pos = pos or stock_position(run, flavor, depot, 400)
    lots = pos.lots.copy()
    today = run.day
    if lots.empty and pos.inbound.empty:
        return pd.DataFrame(columns=["lot_id", "production_date", "qty_reported", "qty_now", "age_days", "days_left_mtqc", "days_left_total"])
    lots = lots.rename(columns={"qty_bottles": "qty_reported"})
    arrived = run.snap.in_transit
    arrived = arrived[(arrived.flavor_id == flavor) & (arrived.destination_id == depot) & (arrived.eta >= pos.report_date) & (arrived.eta < today)]
    if len(arrived):
        pdates = run.snap.lots.set_index("lot_id")["production_date"]
        add = arrived.groupby("lot_id", as_index=False)["qty_bottles"].sum()
        add["production_date"] = add["lot_id"].map(pdates)
        add = add.rename(columns={"qty_bottles": "qty_received"})
        lots = lots.merge(add, how="outer", on=["lot_id", "production_date"])
        lots["qty_reported"] = lots["qty_reported"].fillna(0.0)
        lots["qty_received"] = lots["qty_received"].fillna(0.0)
    else:
        lots["qty_received"] = 0.0
    lots["production_date"] = pd.to_datetime(lots["production_date"])
    lots["qty_pre"] = lots["qty_reported"] + lots["qty_received"]
    lots = lots.sort_values("production_date").reset_index(drop=True)
    lots["age_days"] = (today - lots["production_date"]).dt.days
    lots["days_left_mtqc"] = MTQC_WINDOW - lots["age_days"]
    lots["days_left_total"] = SHELF - lots["age_days"]
    # outflow since report by channel
    out_gt_mt = float(pos.gtmt_est_since.get("p50", 0.0))
    # split GT / MT using the depot's recent dispatch mix
    wk = run.weekly
    gt_share = 0.7
    if len(wk):
        r = wk[(wk.flavor_id == flavor) & (wk.depot_id == depot)].tail(4)
        g, m = float(r["disp_GT"].sum()), float(r["disp_MT"].sum())
        gt_share = g / (g + m) if (g + m) > 0 else 0.7
    out_gt, out_mt, out_qc = out_gt_mt * gt_share, out_gt_mt * (1 - gt_share), pos.qc_observed_since
    qty = lots["qty_pre"].to_numpy(dtype=float).copy()
    elig = (lots["days_left_mtqc"] > 0).to_numpy()

    def draw(amount: float, mask: np.ndarray) -> None:
        nonlocal qty
        for i in np.where(mask)[0]:
            if amount <= 0:
                break
            take = min(qty[i], amount)
            qty[i] -= take
            amount -= take

    draw(out_gt, np.ones(len(qty), dtype=bool))
    draw(out_mt + out_qc, elig)
    lots["qty_now"] = qty
    return lots


def sellable_clock(run: EngineRun, flavor: str, depot: str, pos: Position | None = None, lots: pd.DataFrame | None = None) -> dict:
    """C7: bottle age and days left until stock becomes unsellable to MT/QC (60-day rule on a 90-day life)."""
    lots = lots if lots is not None else lots_now(run, flavor, depot, pos)
    live = lots[lots["qty_now"] > 1.0]
    if live.empty:
        return {"lots": [], "oldest_eligible_days_left": None, "buckets": [], "at_risk_mtqc": 0.0, "total_qty": 0.0}
    elig = live[live["days_left_mtqc"] > 0]
    buckets = []
    edges = [(0, 7, "1-7 days left"), (7, 14, "8-14 days"), (14, 21, "15-21 days"), (21, 31, "22-30 days")]
    for lo, hi, lab in edges:
        sel = elig[(elig["days_left_mtqc"] > lo) & (elig["days_left_mtqc"] <= hi)]
        buckets.append({"label": lab, "qty": float(sel["qty_now"].sum()), "lo": lo, "hi": hi})
    gt_only = live[live["days_left_mtqc"] <= 0]
    buckets.append({"label": "GT-only (under 60 days left)", "qty": float(gt_only["qty_now"].sum()), "lo": -90, "hi": 0})
    # at-risk: eligible bottles that will not sell to MT/QC before the cutoff at the P50 MT+QC rate (FEFO order)
    d = get_demand_for_inventory(run, flavor, depot, "ALL", 28, 0, n=300)
    mtqc_rate = float((d["by_channel"]["MT"] + d["by_channel"]["QC"]).sum(axis=1).mean() / 28)
    at_risk = 0.0
    ahead = 0.0
    for r in elig.sort_values("days_left_mtqc").itertuples():
        sellable_by_cutoff = max(r.days_left_mtqc * mtqc_rate - ahead, 0.0)
        at_risk += max(r.qty_now - sellable_by_cutoff, 0.0)
        ahead += r.qty_now
    rows = [{"lot_id": r.lot_id, "production_date": str(r.production_date.date()), "qty": float(r.qty_now), "age_days": int(r.age_days),
             "days_left_mtqc": int(r.days_left_mtqc), "days_left_total": int(r.days_left_total)}
            for r in live.sort_values("production_date").itertuples()]
    return {"lots": rows, "oldest_eligible_days_left": int(elig["days_left_mtqc"].min()) if len(elig) else 0, "buckets": buckets,
            "at_risk_mtqc": float(at_risk), "total_qty": float(live["qty_now"].sum()), "mtqc_rate_per_day": mtqc_rate,
            "rule": "MT and QC reject stock with under 60 of 90 days left (CASE). GT rule unverified: GT may take any age (ASSUMED).",
            "provenance": ["OBSERVED", "ESTIMATED"]}


def fefo_plan(run: EngineRun, flavor: str, depot: str, pos: Position | None = None, lots: pd.DataFrame | None = None) -> dict:
    """C10: ship order. Lots sorted by days left; GT-only lots go to GT first; lots nearing the MT/QC cutoff go to MT/QC first."""
    lots = lots if lots is not None else lots_now(run, flavor, depot, pos)
    live = lots[lots["qty_now"] > 1.0].sort_values("production_date")
    plan = []
    for r in live.itertuples():
        if r.days_left_mtqc <= 0:
            action = "Ship to GT first: no longer accepted by MT or QC"
            channel = "GT"
        elif r.days_left_mtqc <= 7:
            action = f"Ship to MT/QC now: {int(r.days_left_mtqc)} days left before MT/QC reject it"
            channel = "MT/QC"
        else:
            action = "Ship after the lots above"
            channel = "ANY"
        plan.append({"order": len(plan) + 1, "lot_id": r.lot_id, "qty": float(r.qty_now), "age_days": int(r.age_days),
                     "days_left_mtqc": int(r.days_left_mtqc), "days_left_total": int(r.days_left_total), "channel": channel, "action": action})
    return {"plan": plan, "rule": "FEFO: first expiring, first out. Order is a recommendation (PENDING until approved), not an action."}
