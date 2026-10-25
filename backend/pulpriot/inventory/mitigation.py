"""Mitigation options for a depot-flavor shortage (C21): inter-depot transfer, plant replenishment, co-packer order, do nothing.

Every option is scored on the same axes: arrival date relative to the depot's earliest-likely (P10) stockout date, cost per
bottle, and bottles delivered. Lost sales are simulated day by day from the same demand paths, so avoided loss is computed
once per option and is never double-counted with the delay clock or the waterfall."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..engine.hierarchy import build_paths
from ..engine.pipeline import EngineRun
from .policy import plant_lane
from .position import Position, stock_position, stockout_distribution
from .transfer import lane


def simulate_lost(D: np.ndarray, stock0: np.ndarray, arrivals: np.ndarray) -> np.ndarray:
    """Lost bottles per path over D's horizon: stock_t = stock + arrivals - sales; sales_t = min(D_t, available); no backlog."""
    n, H = D.shape
    stock = stock0.copy()
    lost = np.zeros(n)
    for t in range(H):
        avail = stock + arrivals[t]
        sold = np.minimum(D[:, t], avail)
        lost += D[:, t] - sold
        stock = avail - sold
    return lost


def arrivals_vector(run: EngineRun, pos: Position, horizon: int, extra: list[tuple[pd.Timestamp, float]] | None = None) -> np.ndarray:
    arr = np.zeros(horizon)
    for r in pos.inbound.itertuples():
        k = int((pd.Timestamp(r.eta).normalize() - run.day).days)
        if 0 <= k < horizon:
            arr[k] += r.qty_bottles
    for eta, qty in (extra or []):
        k = int((pd.Timestamp(eta).normalize() - run.day).days)
        if 0 <= k < horizon:
            arr[k] += qty
    return arr


def quick_cover(run: EngineRun, flavor: str, depot: str, n: int = 200) -> dict:
    pos = stock_position(run, flavor, depot, n)
    so = stockout_distribution(run, flavor, depot, 60, n, pos=pos)
    return {"depot": depot, "pos": pos, "so": so,
            "stock": float(np.median(pos.nowcast_paths)) if pos.nowcast_paths is not None else 0.0,
            "daily": so["mean_daily_demand_14d"]}


def plant_fg(run: EngineRun, flavor: str, plant: str | None = None) -> dict:
    fg = run.snap.plant_fg
    fg = fg[(fg.flavor_id == flavor) & (fg.plant_id.isin(["PLT1", "PLT2"]))]
    if plant:
        fg = fg[fg.plant_id == plant]
    if fg.empty:
        return {"qty": 0.0, "week_start": None}
    last = fg[fg.week_start == fg.week_start.max()]
    return {"qty": float(last["fg_bottles"].sum()), "week_start": str(last["week_start"].max().date()), "by_plant": last.groupby("plant_id")["fg_bottles"].sum().to_dict()}


def copacker_headroom(run: EngineRun) -> dict:
    """Up to 10% of in-house monthly capacity, less what the co-packer already makes this month (T09)."""
    plants = run.ds.dims["plant"]
    inhouse = float(plants[plants.type == "in-house"]["capacity_bottles_month"].sum())
    cap = inhouse * run.params["copacker_max_volume_share"]
    pr = run.snap.production
    used = float(pr[(pr.plant_id == "CPK1") & (pr.week_start >= run.day.replace(day=1))]["actual_bottles"].sum())
    return {"monthly_cap": cap, "used_this_month": used, "available": max(cap - used, 0.0), "inhouse_capacity": inhouse}


def mitigation_options(run: EngineRun, flavor: str, depot: str, target_days: int = 21, n: int = 400,
                       economics: str = "margin", pos_cache: dict | None = None, constraints: dict | None = None) -> dict:
    """`constraints` carries what earlier recommendations in the same run already claimed, so one pile of finished goods or
    one donor's surplus is never promised twice: {"fg_used": {flavor: bottles}, "donor_used": {(flavor, depot): bottles},
    "copacker_used": bottles}."""
    p = run.params
    cons = constraints if constraints is not None else {}
    fg_used = cons.get("fg_used", {})
    donor_used = cons.get("donor_used", {})
    horizon = max(target_days + 14, 35)
    pc = pos_cache if pos_cache is not None else {}
    if depot not in pc:
        pc[depot] = stock_position(run, flavor, depot, n)
    pos = pc[depot]
    so = stockout_distribution(run, flavor, depot, 90, n, pos=pos)
    ps = build_paths(run, flavor, depot, days=horizon, n=n)
    D = ps.total
    stock0 = pos.nowcast_paths
    base_arr = arrivals_vector(run, pos, horizon)
    lost0 = simulate_lost(D, stock0, base_arr)
    window = D[:, :target_days].sum(axis=1)
    supply = stock0 + base_arr[:target_days].sum()
    need = float(max(np.quantile(window, p["service_level"]) - np.median(supply), 0.0))
    unit = p["price"] * (p["margin"] if economics == "margin" else 1.0)
    p10 = pd.Timestamp(so["date_p10"])
    p50 = pd.Timestamp(so["date_p50"])
    options: list[dict] = []

    def add(key, label, qty, arrival, cost_pb, basis, feasible=True, notes=None, extra_detail=None):
        qty = float(max(qty, 0.0))
        if qty > 0 and arrival is not None and feasible:
            lost = simulate_lost(D, stock0, arrivals_vector(run, pos, horizon, [(arrival, qty)]))
            avoided = lost0 - lost
        else:
            avoided = np.zeros(len(D))
        late = arrival is None or pd.Timestamp(arrival) > p10
        options.append({
            "key": key, "label": label, "qty": qty, "arrival_date": None if arrival is None else str(pd.Timestamp(arrival).date()),
            "days_vs_p10_stockout": None if arrival is None else int((pd.Timestamp(arrival) - p10).days),
            "days_vs_p50_stockout": None if arrival is None else int((pd.Timestamp(arrival) - p50).days),
            "arrives_too_late": bool(late), "cost_per_bottle": float(cost_pb), "cost_total": float(cost_pb * qty),
            "avoided_lost_bottles_p50": float(np.median(avoided)), "avoided_lost_bottles_mean": float(avoided.mean()),
            "avoided_value_mean": float(avoided.mean() * unit), "net_value_mean": float(avoided.mean() * unit - cost_pb * qty),
            "basis": basis, "feasible": bool(feasible and qty > 0), "notes": notes or [], "detail": extra_detail or {},
        })

    # 1. inter-depot transfer
    donors = []
    for dd in run.ds.depots:
        if dd == depot:
            continue
        ln = lane(run, dd, depot)
        if ln is None:
            continue
        if dd not in pc:
            pc[dd] = stock_position(run, flavor, dd, n)
        dpos = pc[dd]
        dso = stockout_distribution(run, flavor, dd, 60, n, pos=dpos)
        stock_d = float(np.median(dpos.nowcast_paths)) if dpos.nowcast_paths is not None else 0.0
        keep = dso["mean_daily_demand_14d"] * p["transfer_donor_min_cover_days"]
        surplus = max(stock_d - keep - donor_used.get((flavor, dd), 0.0), 0.0)
        donors.append({"depot": dd, "surplus": surplus, "transit_days": ln["transit_days"], "cost_per_bottle": ln["cost_per_bottle"],
                       "stock": stock_d, "cover_p50": dso["days_p50"]})
    donors = [d for d in donors if d["surplus"] > 0]
    donors.sort(key=lambda d: (d["transit_days"], -d["surplus"]))
    if donors and need > 0:
        best = donors[0]
        qty = min(need, best["surplus"])
        arr = run.day + pd.Timedelta(days=best["transit_days"])
        add("transfer", f"Transfer from {best['depot']}", qty, arr, best["cost_per_bottle"],
            f"INR {best['cost_per_bottle']:.2f}/bottle (CASE) + {best['transit_days']} day lane (SYNTHETIC lane table)",
            extra_detail={"donor": best["depot"], "surplus": best["surplus"], "other_donors": donors[1:4]})
    else:
        add("transfer", "Transfer from another depot", 0, None, p["transfer_cost_per_bottle"],
            "No depot holds more than its minimum cover of this flavor", feasible=False,
            notes=["No donor with surplus: every other depot is at or below the donor minimum cover."])

    # 2. plant replenishment (existing finished goods; plants cannot add lines mid-season)
    lane_p = plant_lane(run, depot)
    fg = plant_fg(run, flavor)
    fg = {**fg, "qty": max(fg["qty"] - fg_used.get(flavor, 0.0), 0.0)}
    qty_p = min(need, fg["qty"])
    if qty_p > 0:
        add("plant", f"Ship from {lane_p['plant']} finished goods", qty_p, run.day + pd.Timedelta(days=1 + lane_p["transit_days"]),
            lane_p["freight_per_bottle"], f"Lane freight INR {lane_p['freight_per_bottle']:.2f}/bottle (SYNTHETIC); dispatch next day (ASSUMED)",
            extra_detail={"fg": fg, "plant": lane_p["plant"]})
    else:
        add("plant", "Ship from plant finished goods", 0, None, lane_p["freight_per_bottle"],
            "No finished goods of this flavor at the plants in the latest plant report; plant capacity is fixed in summer", feasible=False,
            notes=["Plants cannot add mid-season lines (CASE)."], extra_detail={"fg": fg})

    # 3. co-packer
    cp = copacker_headroom(run)
    unit_cost = p["price"] * (1 - p["margin"])
    premium = unit_cost * p["copacker_cost_premium"]
    cp_lane = run.ds.dims["lane_plant"]
    cpl = cp_lane[cp_lane.depot_id == depot].sort_values("transit_days")
    transit = int(cpl.iloc[0]["transit_days"]) if len(cpl) else 3
    freight = float(cpl.iloc[0]["freight_per_bottle"]) if len(cpl) else 1.5
    cp = {**cp, "available": max(cp["available"] - cons.get("copacker_used", 0.0), 0.0)}
    qty_c = min(need, cp["available"])
    arr_c = run.day + pd.Timedelta(days=p["copacker_notice_days"] + transit)
    add("copacker", "Co-packer order", qty_c, arr_c, premium + freight,
        f"+25% on in-house cost: INR {unit_cost:.1f} x 25% = INR {premium:.1f}/bottle (CASE premium; base cost DERIVED from price x (1 - margin)) "
        f"+ freight INR {freight:.2f} (lane ASSUMED from nearest plant); {p['copacker_notice_days']} days notice (CASE)",
        extra_detail={"headroom": cp, "notice_days": p["copacker_notice_days"]})

    # 4. do nothing
    add("nothing", "Do nothing", 0, None, unit, f"Each lost bottle costs INR {unit:.1f} ({'contribution margin' if economics == 'margin' else 'revenue'}, CASE price x 32% margin)",
        feasible=False, notes=["Shortfall goes unfilled."])
    options[-1]["bottles_at_risk"] = float(lost0.mean())
    options[-1]["feasible"] = True
    return {"flavor": flavor, "depot": depot, "need_bottles": need, "target_days": target_days,
            "stockout": {k: so[k] for k in ("date_p10", "date_p50", "date_p90", "days_p10", "days_p50", "days_p90")},
            "lost_if_nothing": {"mean": float(lost0.mean()), "p50": float(np.median(lost0)), "p90": float(np.quantile(lost0, .9))},
            "economics": economics, "unit_value": unit, "options": options, "interval_type": ps.interval_type,
            "provenance": ["SYNTHETIC", "ESTIMATED", "CALCULATED"]}
