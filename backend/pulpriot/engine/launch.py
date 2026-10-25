"""M6 cold start for a flavor with no sales history.

Both Dragon Passion analogs (Dragonfruit Bloom, Passionfruit Punch) launched before the workbook's data window, so
their first-12-week curves do not exist. Per spec v1.1 each analog curve is the generic launch template (R03
`sales_index_vs_steady_state`, ASSUMED) multiplied by that analog's run-rate in the same season last year (T01).
Similarity scores are manual 0-1 inputs (config, ASSUMED) and are shown in the UI with the analog list.

P(w) = scale * sum_i s_i A_i(w) / sum_i s_i,  A_i(w) = template(w) * run-rate_i
After n observed uncensored weeks:  delta = mean(log obs - log P);  shift = tau_o n delta / (tau_p + n tau_o)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data.loader import Dataset, Snapshot
from ..settings import Params
from . import tags_local as TL


def _template(ds: Dataset) -> np.ndarray:
    r = ds.refs["R03"].sort_values("week_since_launch")
    return r["sales_index_vs_steady_state"].to_numpy(dtype=float)


def template_at(days_since_launch: np.ndarray, tmpl: np.ndarray, interp: bool = False) -> np.ndarray:
    """Launch template by day since launch.

    interp=True follows the spec text (linear between week centres, no weekly steps). The supplied launch data moves in
    weekly steps, so the default is the week-constant template (config launch_curve_interp = false, tested both ways)."""
    if interp:
        centres = (np.arange(len(tmpl)) + 0.5) * 7.0
        return np.interp(days_since_launch, centres, tmpl)
    wk = np.clip(np.floor(np.asarray(days_since_launch) / 7.0).astype(int), 0, len(tmpl) - 1)
    return tmpl[wk]


def similarity(scores: dict[str, float]) -> float:
    """s_i = mean of the five manual scores: ingredient overlap, price point, channel mix, launch season, promo intensity."""
    return float(np.mean(list(scores.values())))


@dataclass
class LaunchModel:
    flavor: str
    launch_date: pd.Timestamp
    analogs: list[dict]                  # id, scores, similarity, name
    sigma_p: float
    template: np.ndarray
    rates: dict                          # (analog, depot, channel) -> Series of monthly daily-rate indexed by month start
    scale: float = 1.0                   # multiplier on the default scale (analog week-4 units)
    interp: bool = False
    notes: list[str] = field(default_factory=list)

    # ---- analog run-rates -------------------------------------------------------------------------
    def _runrate(self, analog: str, depots: list[str], channel: str, dates: pd.DatetimeIndex) -> np.ndarray:
        """Daily rate of the analog at the node, same season last year, interpolated between month midpoints."""
        memo = self.__dict__.setdefault("_rr_memo", {})
        mk = (analog, tuple(depots), channel, dates[0].value if len(dates) else 0, len(dates))
        hit = memo.get(mk)
        if hit is not None:
            return hit
        out = self._runrate_calc(analog, depots, channel, dates)
        memo[mk] = out
        return out

    def _runrate_calc(self, analog: str, depots: list[str], channel: str, dates: pd.DatetimeIndex) -> np.ndarray:
        keys = [(analog, d, channel) for d in depots if (analog, d, channel) in self.rates]
        if not keys:
            return np.zeros(len(dates))
        tot = None
        for k in keys:
            s = self.rates[k]
            tot = s if tot is None else tot.add(s, fill_value=0.0)
        mids = (tot.index + pd.to_timedelta(tot.index.days_in_month / 2.0, unit="D")).values.astype("int64")
        shifted = (dates - pd.DateOffset(years=1)).values.astype("int64")
        return np.interp(shifted, mids, tot.to_numpy())

    def prior_daily(self, depots: list[str], channel: str, dates: pd.DatetimeIndex) -> np.ndarray:
        """P0(d): similarity-weighted analog curve at the node before any shift (units per day)."""
        w = np.array([a["similarity"] for a in self.analogs])
        w = w / w.sum()
        age = ((dates - self.launch_date).days).to_numpy().astype(float)
        t = template_at(np.maximum(age, 0.0), self.template, self.interp)
        out = np.zeros(len(dates))
        for wi, a in zip(w, self.analogs):
            out += wi * self._runrate(a["id"], depots, channel, dates) * t
        out *= self.scale
        out[age < 0] = 0.0
        return out

    # ---- Bayesian shift ---------------------------------------------------------------------------
    def shift(self, obs_weekly: np.ndarray, prior_weekly: np.ndarray) -> tuple[float, float]:
        """(shift, sd_post) from n observed weeks. n = 0 returns the prior exactly."""
        ok = np.isfinite(obs_weekly) & np.isfinite(prior_weekly) & (obs_weekly > 0) & (prior_weekly > 0)
        n = int(ok.sum())
        tau_p = 1.0 / self.sigma_p ** 2
        if n == 0:
            return 0.0, 1.0 / np.sqrt(tau_p)
        delta = float(np.mean(np.log(obs_weekly[ok]) - np.log(prior_weekly[ok])))
        tau_o = 1.0 / 0.25 ** 2
        return tau_o * n * delta / (tau_p + n * tau_o), 1.0 / np.sqrt(tau_p + n * tau_o)

    def weekly_totals(self, dates: pd.DatetimeIndex, values: np.ndarray, uncensored: np.ndarray, upto: pd.Timestamp
                      ) -> tuple[np.ndarray, np.ndarray]:
        """Complete launch weeks (Mon-aligned from launch) ending on or before `upto`, as (observed, prior-input mask)."""
        obs, idx = [], []
        w = 0
        while True:
            a = self.launch_date + pd.Timedelta(days=7 * w)
            b = a + pd.Timedelta(days=6)
            if b > upto:
                break
            sel = (dates >= a) & (dates <= b)
            if sel.sum() == 7 and uncensored[sel].all():
                obs.append(float(values[sel].sum()))
            else:
                obs.append(np.nan)
            idx.append(w)
            w += 1
        return np.array(obs), np.array(idx)


def build_launch_model(ds: Dataset, snap: Snapshot, flavor: str, params: Params,
                       similarity_override: dict | None = None, scale: float = 1.0) -> LaunchModel | None:
    row = ds.dims["flavor"].set_index("flavor_id").loc[flavor]
    ld = pd.to_datetime(row["launch_date"], errors="coerce")
    if pd.isna(ld):
        return None
    sims = params["analog_similarity"].get(flavor)
    if not sims:
        return None
    if similarity_override:
        sims = {a: {**sims[a], **similarity_override.get(a, {})} for a in sims}
    names = ds.flavor_name()
    analogs = [{"id": a, "name": names.get(a, a), "scores": sc, "similarity": similarity(sc)} for a, sc in sims.items()]
    sm = snap.sales_monthly
    rates: dict = {}
    for (fl, dep, ch), g in sm[sm.flavor_id.isin([a["id"] for a in analogs])].groupby(["flavor_id", "depot_id", "channel_id"]):
        s = pd.Series((g["bottles_sold"] / g["month"].dt.days_in_month).to_numpy(), index=pd.DatetimeIndex(g["month"]))
        rates[(fl, dep, ch)] = s.sort_index()
    mean_s = float(np.mean([a["similarity"] for a in analogs]))
    sigma_p = float(np.sqrt(params["sigma_a"] ** 2 + params["sigma_b"] ** 2 / mean_s))
    notes = [TL.ANALOG_OUTSIDE_WINDOW, TL.TEMPLATE_ASSUMED, TL.EXTRAPOLATED]
    return LaunchModel(flavor, ld, analogs, sigma_p, _template(ds), rates, scale, bool(params.get('launch_curve_interp', False)), notes)


def build_hypothetical_launch(ds: Dataset, snap: Snapshot, analog_scores: dict[str, dict], launch_date: pd.Timestamp, params: Params,
                              scale: float = 1.0, flavor_id: str = "NEW") -> LaunchModel | None:
    """B8 what-if: a flavor that does not exist yet, modelled on chosen analogs with manual similarity scores (ASSUMED)."""
    if not analog_scores:
        return None
    names = ds.flavor_name()
    analogs = [{"id": a, "name": names.get(a, a), "scores": sc, "similarity": similarity(sc)} for a, sc in analog_scores.items()]
    sm = snap.sales_monthly
    rates: dict = {}
    for (fl, dep, ch), g in sm[sm.flavor_id.isin([a["id"] for a in analogs])].groupby(["flavor_id", "depot_id", "channel_id"]):
        s = pd.Series((g["bottles_sold"] / g["month"].dt.days_in_month).to_numpy(), index=pd.DatetimeIndex(g["month"]))
        rates[(fl, dep, ch)] = s.sort_index()
    mean_s = float(np.mean([a["similarity"] for a in analogs]))
    sigma_p = float(np.sqrt(params["sigma_a"] ** 2 + params["sigma_b"] ** 2 / max(mean_s, 0.05)))
    return LaunchModel(flavor_id, pd.Timestamp(launch_date), analogs, sigma_p, _template(ds), rates, scale,
                       bool(params.get("launch_curve_interp", False)), [TL.ANALOG_OUTSIDE_WINDOW, TL.TEMPLATE_ASSUMED, TL.EXTRAPOLATED])
