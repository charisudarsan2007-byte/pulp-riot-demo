"""Stock position, nowcast and cover.

Reported stock is a fact (OBSERVED, dated by the weekly report). Today's stock is an ESTIMATE:

    nowcast = last reported stock + receipts due since the report - outflow since the report

QC outflow since the report is the observed QC sell-through (PROXY for dispatch to dark stores, units arrive next day);
GT and MT outflow is the engine's phased estimate, drawn as sample paths so the uncertainty carries into the stock level.
Stock can never go below zero: demand is latent, fulfilment is capped here (not in the demand engine)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..engine.common import summarise
from ..engine.forecast import censored_share
from ..engine.hierarchy import build_paths
from ..engine.pipeline import EngineRun


@dataclass
class Position:
    flavor: str
    depot: str
    report_date: pd.Timestamp | None
    report_received_at: pd.Timestamp | None
    reported_qty: float
    lots: pd.DataFrame
    receipts_since: float
    receipts_detail: list[dict]
    qc_observed_since: float
    gtmt_est_since: dict
    nowcast_paths: np.ndarray | None          # n starting-stock paths for today
    nowcast: dict
    inbound: pd.DataFrame                      # confirmed shipments arriving today or later
    gap_days: int
    n_days_lookback: int


def last_report(run: EngineRun, flavor: str, depot: str) -> tuple[pd.Timestamp | None, pd.DataFrame, pd.Timestamp | None]:
    st = run.snap.stock_weekly
    rep = st[st.depot_id == depot]
    if rep.empty:
        return None, pd.DataFrame(), None
    d = rep["stock_date"].max()
    all_lots = rep[rep.stock_date == d]
    recv = all_lots["report_received_at"].max()
    lots = all_lots[all_lots.flavor_id == flavor][["lot_id", "production_date", "qty_bottles"]].copy()
    return d, lots, recv


def inbound_shipments(run: EngineRun, flavor: str, depot: str) -> pd.DataFrame:
    """Shipments dispatched and known by the as-of moment, with their ETAs (planner view, not hindsight)."""
    it = run.snap.in_transit
    x = it[(it.flavor_id == flavor) & (it.destination_id == depot)]
    return x[["shipment_id", "movement", "origin_id", "qty_bottles", "dispatch_date", "eta", "lot_id"]].copy()


def stock_position(run: EngineRun, flavor: str, depot: str, n: int = 600) -> Position:
    cache = run.__dict__.setdefault("_poscache", {})
    key = (flavor, depot, n)
    if key not in cache:
        cache[key] = _stock_position(run, flavor, depot, n)
    return cache[key]


def _stock_position(run: EngineRun, flavor: str, depot: str, n: int) -> Position:
    S, lots, recv = last_report(run, flavor, depot)
    today = run.day
    if S is None:
        return Position(flavor, depot, None, None, 0.0, lots, 0.0, [], 0.0, {}, None, {}, pd.DataFrame(), 0, 0)
    gap = int((today - S).days)
    reported = float(lots["qty_bottles"].sum()) if len(lots) else 0.0
    inb = inbound_shipments(run, flavor, depot)
    arrived = inb[(inb.eta >= S) & (inb.eta < today)]
    pending = inb[inb.eta >= today]
    rec_by_day = arrived.groupby(arrived["eta"].dt.normalize())["qty_bottles"].sum()
    receipts = float(rec_by_day.sum())
    detail = [{"date": str(d.date()), "qty": float(v)} for d, v in rec_by_day.items()]
    # observed QC sell-through at this depot since the report
    q = run.snap.qc_daily
    q = q[(q.flavor_id == flavor) & (q.depot_id == depot) & (q.date >= S) & (q.date < today)]
    qc_by_day = q.set_index("date")["units_sold"].reindex(pd.date_range(S, periods=max(gap, 0))).fillna(0.0).to_numpy()
    qc_obs = float(qc_by_day.sum())
    if gap <= 0:
        paths = np.full(n, reported)
        return Position(flavor, depot, S, recv, reported, lots, receipts, detail, qc_obs, {"p50": 0.0}, paths,
                        {**summarise(paths), "reported": reported}, pending, gap, 0)
    ps = build_paths(run, flavor, depot, days=gap, n=n, start=S)
    gtmt = (ps.by_channel["GT"] + ps.by_channel["MT"])                        # n x gap latent GT+MT demand
    rec_arr = np.array([rec_by_day.get(pd.Timestamp(d), 0.0) for d in ps.dates])
    stock = np.full(n, reported)
    out_gtmt = np.zeros(n)
    for j in range(gap):
        avail = stock + rec_arr[j]
        want = gtmt[:, j] + qc_by_day[j]
        sold = np.minimum(want, avail)
        out_gtmt += np.minimum(gtmt[:, j], sold)
        stock = avail - sold
    return Position(flavor, depot, S, recv, reported, lots, receipts, detail, qc_obs,
                    {**summarise(out_gtmt)}, stock, {**summarise(stock), "reported": reported}, pending, gap, gap)


def stockout_distribution(run: EngineRun, flavor: str, depot: str, horizon: int = 90, n: int = 600, surge: float = 1.0,
                          rho: float | None = None, pos: Position | None = None, include_inbound: bool = True,
                          extra_arrivals: list[tuple[pd.Timestamp, float]] | None = None, stock_delta: float = 0.0) -> dict:
    """Days until on-hand stock (nowcast + confirmed inbound) is exhausted by latent demand, per path.

    No further replenishment is assumed beyond shipments already dispatched. Returns the distribution of the stockout day."""
    pos = pos or stock_position(run, flavor, depot, n)
    ck = None
    if not extra_arrivals and stock_delta == 0.0:
        ck = ("so", flavor, depot, horizon, len(pos.nowcast_paths) if pos.nowcast_paths is not None else n, surge, rho, include_inbound)
        cache = run.__dict__.setdefault("_socache", {})
        if ck in cache:
            return cache[ck]
    if pos.nowcast_paths is not None:
        n = len(pos.nowcast_paths)                      # paths must line up with the nowcast draws
    ps = build_paths(run, flavor, depot, days=horizon, n=n, surge=surge, rho=rho)
    D = ps.total
    stock0 = (pos.nowcast_paths if pos.nowcast_paths is not None else np.zeros(n)) + stock_delta
    stock0 = np.maximum(stock0, 0.0)
    inb = pos.inbound
    arr = np.zeros(horizon)
    for eta, qty in (extra_arrivals or []):
        k = int((pd.Timestamp(eta).normalize() - run.day).days)
        if 0 <= k < horizon:
            arr[k] += qty
    if include_inbound and len(inb):
        for r in inb.itertuples():
            k = int((pd.Timestamp(r.eta).normalize() - run.day).days)
            if 0 <= k < horizon:
                arr[k] += r.qty_bottles
    cum_supply = stock0[:, None] + np.cumsum(arr)[None, :]
    cum_dem = np.cumsum(D, axis=1)
    short = cum_dem > cum_supply
    first = np.where(short.any(axis=1), short.argmax(axis=1), horizon)          # day index (0 = today)
    # fractional day within the stockout day
    prev_sup = np.where(first > 0, cum_supply[np.arange(n), np.maximum(first - 1, 0)] - cum_dem[np.arange(n), np.maximum(first - 1, 0)], stock0)
    day_dem = D[np.arange(n), np.minimum(first, horizon - 1)]
    frac = np.clip(prev_sup / np.maximum(day_dem, 1e-9), 0, 1)
    t = np.where(first < horizon, first + frac, float(horizon))
    never = float((first >= horizon).mean())
    q = lambda p: float(np.quantile(t, p))
    dts = lambda x: run.day + pd.Timedelta(days=float(x))
    mean_daily = float(D[:, :14].sum(axis=1).mean() / 14)
    reported_stock = float(np.median(stock0))
    res = {
        "days_p10": q(.10), "days_p50": q(.50), "days_p90": q(.90),
        "date_p10": str(dts(q(.10)).date()), "date_p50": str(dts(q(.50)).date()), "date_p90": str(dts(q(.90)).date()),
        "share_beyond_horizon": never, "horizon": horizon,
        "beyond_horizon_p50": bool(q(.5) >= horizon), "mean_daily_demand_14d": mean_daily,
        "simple_cover_days": reported_stock / mean_daily if mean_daily > 0 else None,
        "stock_today_p50": reported_stock,
        "interval_type": ps.interval_type, "warnings": ps.warnings,
        "definition": "Days until nowcast stock plus confirmed inbound shipments is exhausted by forecast demand; no further replenishment assumed.",
        "lower_bound_only": censored_share(run, flavor) > run.params["censored_fraction_limit"],
    }
    if ck is not None:
        run.__dict__["_socache"][ck] = res
    return res


def recent_runrate_cover(run: EngineRun, flavor: str, depot: str, pos: Position | None = None) -> dict:
    """Cover at the observed run-rate of the last four reported weeks (CALCULATED from reported data, no forecast)."""
    wk = run.weekly
    pos = pos or stock_position(run, flavor, depot, 200)
    if wk.empty:
        return {"days": None}
    r = wk[(wk.flavor_id == flavor) & (wk.depot_id == depot)].sort_values("week_start").tail(4)
    if r.empty:
        return {"days": None}
    daily = float(r["implied_outflow"].clip(lower=0).sum() / (7.0 * len(r)))
    stock = float(np.median(pos.nowcast_paths)) if pos.nowcast_paths is not None else pos.reported_qty
    return {"days": stock / daily if daily > 0 else None, "daily_outflow": daily, "weeks": int(len(r)),
            "basis": "mean implied outflow of the last four reported weeks"}
