"""Transfer preview (C12) and donor/recipient screening for rebalancing lanes (A8).

A transfer recommendation always shows: donor stock and cover after the move, recipient need, arrival date, transit time,
cost at the case's INR 1.50 per bottle, whether it arrives before the recipient's earliest-likely (P10) stockout date,
and how many of the bottles would arrive too old for MT/QC. It stays PENDING until a named stakeholder decides."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..engine.pipeline import EngineRun
from .policy import MTQC_WINDOW, lots_now, safety_stock
from .position import Position, stock_position, stockout_distribution


def lane(run: EngineRun, a: str, b: str) -> dict | None:
    L = run.ds.dims["lane_depot"]
    r = L[(L.from_depot == a) & (L.to_depot == b)]
    if r.empty:
        return None
    r = r.iloc[0]
    return {"transit_days": int(r["transit_days"]), "cost_per_bottle": float(r["cost_per_bottle"])}


def fefo_pick(lots: pd.DataFrame, qty: float) -> pd.DataFrame:
    """Oldest lots first until `qty` is covered."""
    live = lots[lots["qty_now"] > 1.0].sort_values("production_date")
    out, left = [], qty
    for r in live.itertuples():
        if left <= 0:
            break
        take = min(r.qty_now, left)
        out.append({"lot_id": r.lot_id, "qty": float(take), "age_days": int(r.age_days), "days_left_mtqc": int(r.days_left_mtqc)})
        left -= take
    return pd.DataFrame(out)


def transfer_preview(run: EngineRun, flavor: str, donor: str, recipient: str, qty: float, n: int = 500,
                     pos_cache: dict | None = None) -> dict:
    p = run.params
    ln = lane(run, donor, recipient)
    if ln is None:
        return {"feasible": False, "reasons": [f"No lane from {donor} to {recipient} in the lane table."], "lane": None}
    pc = pos_cache if pos_cache is not None else {}
    def pos(d: str) -> Position:
        if d not in pc:
            pc[d] = stock_position(run, flavor, d, n)
        return pc[d]
    pd_, pr = pos(donor), pos(recipient)
    arrival = run.day + pd.Timedelta(days=ln["transit_days"])
    d_before = stockout_distribution(run, flavor, donor, 90, n, pos=pd_)
    r_before = stockout_distribution(run, flavor, recipient, 90, n, pos=pr)
    d_after = stockout_distribution(run, flavor, donor, 90, n, pos=pd_, stock_delta=-qty)
    r_after = stockout_distribution(run, flavor, recipient, 90, n, pos=pr, extra_arrivals=[(arrival, qty)])
    donor_stock = float(np.median(pd_.nowcast_paths)) if pd_.nowcast_paths is not None else 0.0
    rec_stock = float(np.median(pr.nowcast_paths)) if pr.nowcast_paths is not None else 0.0
    ss_r = safety_stock(run, flavor, recipient, n=n, pos=pr)
    ss_d = safety_stock(run, flavor, donor, n=n, pos=pd_)
    lots = lots_now(run, flavor, donor, pd_)
    picked = fefo_pick(lots, qty)
    old_at_arrival = 0.0
    if len(picked):
        left_at_arrival = picked["days_left_mtqc"] - ln["transit_days"]
        old_at_arrival = float(picked.loc[left_at_arrival <= 0, "qty"].sum())
    reasons, warns = [], []
    feasible = True
    if qty > donor_stock:
        feasible = False
        reasons.append(f"Donor holds about {donor_stock:,.0f} bottles; {qty:,.0f} requested.")
    donor_cover_after = d_after["days_p50"]
    if d_after["days_p10"] < p["transfer_donor_min_cover_days"]:
        feasible = False
        reasons.append(f"Donor would be left with an earliest-likely stockout in {d_after['days_p10']:.0f} days "
                       f"(minimum {p['transfer_donor_min_cover_days']} days, ASSUMED).")
    arrives_before_p10 = arrival <= pd.Timestamp(r_before["date_p10"])
    arrives_before_p50 = arrival <= pd.Timestamp(r_before["date_p50"])
    if not arrives_before_p10:
        warns.append("ARRIVES_AFTER_P10_STOCKOUT")
    if old_at_arrival > 0:
        warns.append("SOME_BOTTLES_GT_ONLY_ON_ARRIVAL")
    return {
        "flavor": flavor, "donor": donor, "recipient": recipient, "qty": qty, "lane": ln,
        "arrival_date": str(arrival.date()), "transit_days": ln["transit_days"],
        "cost_total": qty * p["transfer_cost_per_bottle"], "cost_per_bottle": p["transfer_cost_per_bottle"],
        "feasible": feasible, "reasons": reasons, "warnings": warns,
        "arrives_before_recipient_p10": bool(arrives_before_p10), "arrives_before_recipient_p50": bool(arrives_before_p50),
        "donor": {"depot": donor, "stock_before": donor_stock, "stock_after": donor_stock - qty,
                  "cover_before": d_before, "cover_after": d_after, "safety_target": ss_d["target_final"],
                  "below_target_after": bool(donor_stock - qty < ss_d["target_final"])},
        "recipient": {"depot": recipient, "stock_before": rec_stock, "stock_after_arrival": rec_stock + qty,
                      "cover_before": r_before, "cover_after": r_after, "safety_target": ss_r["target_final"],
                      "need": max(ss_r["target_final"] - rec_stock - ss_r["inbound_confirmed"], 0.0)},
        "fefo_lots": picked.to_dict("records") if len(picked) else [],
        "gt_only_on_arrival": old_at_arrival,
        "provenance": ["SYNTHETIC", "ESTIMATED", "CALCULATED"], "status": "PENDING (not an action)",
    }
