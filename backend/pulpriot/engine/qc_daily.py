"""M3 daily quick-commerce series and filter helpers.

The workbook QC feed carries `units_sold` and a stockout flag but no `orders_on_company`, so the detection signal is
units_sold (tag UNITS_SOLD_SIGNAL) and flagged days are treated as censored: they may raise the level estimate
(a lower bound that still exceeds the baseline) but never lower it."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.loader import Snapshot
from ..settings import Params
from .common import robust_sigma

SIGNAL_TAG = "UNITS_SOLD_SIGNAL"


@dataclass
class QCSeries:
    flavor: str
    region: str                      # region id or "ALL"
    dates: pd.DatetimeIndex
    q: np.ndarray                    # units sold per day (sum over depots in scope)
    cens: np.ndarray                 # volume-weighted censored fraction of the day, 0..1
    depots: list[str] = field(default_factory=list)
    signal: str = SIGNAL_TAG

    @property
    def censored(self) -> np.ndarray:
        return self.cens >= 0.5


def build_qc_series(snap: Snapshot, depot_region: dict[str, str]) -> dict[tuple[str, str], QCSeries]:
    """One series per (flavor, region) plus one national series per flavor, from arrived QC rows only.

    Censoring-aware aggregation: a stocked-out depot-day is missing, not low. If the uncensored depots carry at least
    half of the expected volume (weights = causal expanding mean of each depot's own uncensored sales), the scope total
    is their sales scaled by their weight share (tag CENSOR_ADJUSTED); otherwise the day is censored and the plain
    sum is kept as a lower bound."""
    q = snap.qc_daily
    if q.empty:
        return {}
    q = q.copy()
    q["region_id"] = q["depot_id"].map(depot_region)
    days = pd.date_range(q["date"].min(), q["date"].max(), freq="D")
    out: dict[tuple[str, str], QCSeries] = {}
    q = q.sort_values(["flavor_id", "depot_id", "date"])
    clean = q["units_sold"].where(q["stockout_flag"] == 0)
    grp = [q["flavor_id"], q["depot_id"]]
    q["w"] = clean.groupby(grp).transform(lambda s: s.expanding().mean()).groupby(grp).ffill().fillna(0.0).clip(lower=1.0)
    q["u_w"] = q["w"] * (q["stockout_flag"] == 0)
    q["u_sold"] = q["units_sold"] * (q["stockout_flag"] == 0)
    for fl, g in q.groupby("flavor_id"):
        scopes = [(r, gr) for r, gr in g.groupby("region_id")] + [("ALL", g)]
        for reg, gr in scopes:
            by = gr.groupby("date").agg(raw=("units_sold", "sum"), w=("w", "sum"), uw=("u_w", "sum"), us=("u_sold", "sum")).reindex(days)
            u = (by["uw"] / by["w"]).fillna(1.0).to_numpy()
            raw = by["raw"].to_numpy(dtype=float)
            scaled = np.where(u >= 0.5, by["us"].to_numpy(dtype=float) / np.maximum(u, 1e-9), raw)
            cens = 1.0 - u
            cens = np.where(cens >= 0.5, cens, 0.0)           # partial censoring below 50% is handled by the scaling
            out[(fl, reg)] = QCSeries(fl, reg, days, scaled, cens, sorted(gr["depot_id"].unique()))
    return out


def weekday_effects(resid0: np.ndarray, dows: np.ndarray, clean: np.ndarray, params: Params) -> np.ndarray:
    """Spec M3: after weekday_min_days clean days, w[j] = n_j / (n_j + shrink) * (raw[j] - mean(raw)); else zeros."""
    m = clean & np.isfinite(resid0)
    if m.sum() < params["weekday_min_days"]:
        return np.zeros(7)
    raw = np.zeros(7)
    n = np.zeros(7)
    for j in range(7):
        sel = m & (dows == j)
        n[j] = sel.sum()
        raw[j] = resid0[sel].mean() if n[j] else 0.0
    mean_raw = raw.mean()
    return n / (n + params["weekday_shrink"]) * (raw - mean_raw)


def pooled_weekday_multipliers(series: dict[tuple[str, str], QCSeries], params: Params, exclude: set[str]) -> np.ndarray:
    """Multiplicative weekday factors (mean 1) pooled over mature national series. ESTIMATED; ones if too little data."""
    effs = []
    for (fl, reg), s in series.items():
        if reg != "ALL" or fl in exclude:
            continue
        z = np.log(s.q + params["kappa"])
        ok = ~s.censored & np.isfinite(z)
        if ok.sum() < params["weekday_min_days"]:
            continue
        dev = z - pd.Series(z).rolling(7, center=True, min_periods=4).mean().to_numpy()
        dows = s.dates.dayofweek.to_numpy()
        eff = np.array([np.nanmean(dev[ok & (dows == j)]) if (ok & (dows == j)).any() else 0.0 for j in range(7)])
        effs.append(eff - eff.mean())
    if not effs:
        return np.ones(7)
    e = np.mean(effs, axis=0)
    m = np.exp(e)
    return m / m.mean()


def noise_sigma(resid: np.ndarray, in_control: np.ndarray, params: Params, launch: bool) -> float:
    """sigma_e = 1.4826 * MAD of in-control residuals over the last sigma_e_window days, floored."""
    w = params["sigma_e_window"]
    r = resid[-w:][in_control[-w:]] if len(resid) else np.array([])
    floor = params["sigma_e_floor_launch"] if launch else params["sigma_e_floor"]
    return robust_sigma(r, floor)
