"""M1 censoring: stockout-capped sales are lower bounds, not demand.

* QC days: p = stockout flag (the spec's oos_flag); the workbook's QC feed has no orders_on_company column, so the
  detection signal is units_sold and every output built on it is tagged UNITS_SOLD_SIGNAL.
* Depot weeks: p from days of cover (spec), raised to 1 when a stockout-log episode overlaps the week.
* d_adj (CENSOR_ADJUSTED) is used only for shares, seasonal masks and lost-demand estimates, never as a level
  observation (red line 5)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.loader import Snapshot
from ..settings import Params


def censor_prob_from_cover(cover_days: float, params: Params) -> float:
    """Spec M1: p = 1 if cover < 1 day, 0.5 if < 3 days, else 0."""
    if not np.isfinite(cover_days):
        return 0.0
    if cover_days < params["censor_cover_red"]:
        return 1.0
    if cover_days < params["censor_cover_amber"]:
        return 0.5
    return 0.0


def qc_depot_days(snap: Snapshot) -> pd.DataFrame:
    """QC rows that had arrived, with the censoring probability attached (p = stockout flag)."""
    q = snap.qc_daily.copy()
    q["p"] = q["stockout_flag"].astype(float)
    return q


def stockout_days(snap: Snapshot) -> pd.DataFrame:
    """Expand arrived stockout-log episodes to (depot, flavor, channel, date)."""
    log = snap.stockout_log
    if log.empty:
        return pd.DataFrame(columns=["depot_id", "flavor_id", "channel_id", "date"])
    rows = []
    for r in log.itertuples():
        for d in pd.date_range(r.start_date, r.end_date):
            rows.append((r.depot_id, r.flavor_id, r.channel_id, d))
    return pd.DataFrame(rows, columns=["depot_id", "flavor_id", "channel_id", "date"])


def monthly_censored_share(snap: Snapshot) -> pd.DataFrame:
    """Share of calendar days in a month on which (flavor, depot, channel) was out of stock, from the stockout log.

    Used to mask months out of seasonal profiles (censored share >= 0.5 gets zero weight)."""
    sd = stockout_days(snap)
    if sd.empty:
        return pd.DataFrame(columns=["month", "flavor_id", "depot_id", "channel_id", "cens_share"])
    sd = sd.drop_duplicates()
    sd["month"] = sd["date"].values.astype("datetime64[M]")
    g = sd.groupby(["month", "flavor_id", "depot_id", "channel_id"]).size().rename("days_out").reset_index()
    g["month"] = pd.to_datetime(g["month"])
    g["cens_share"] = g["days_out"] / g["month"].dt.days_in_month
    return g[["month", "flavor_id", "depot_id", "channel_id", "cens_share"]]


def d_adj(observed: float, p: float, p50_forecast: float) -> tuple[float, bool]:
    """Spec M1: d_adj = observed if p < 0.5 else max(observed, p50 forecast). Returns (value, adjusted?).

    A point estimate for shares and loss only; it must never be fed back as a level observation."""
    if p >= 0.5:
        return max(observed, p50_forecast), True
    return observed, False


def level_update_allowed(p: float, observed: float, p50: float) -> str:
    """How an observation may be used by a level filter.

    p >= 0.5 and observed < p50 -> MISSING (no level update, tagged censored);
    p >= 0.5 and observed >= p50 -> normal update; p < 0.5 -> normal update."""
    if p >= 0.5 and observed < p50:
        return "MISSING"
    return "UPDATE"
