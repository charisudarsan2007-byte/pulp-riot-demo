"""M7 hierarchy: depot shares from censoring-adjusted demand, Monte Carlo sample paths, coherent aggregation.

* Base nodes are flavor x channel x region. A depot forecast is never a fixed fraction: each path draws depot shares
  from Dirichlet(c * pi_hat), so depot paths sum exactly to the region path on every path.
* pi_obs is the time-decayed share of censoring-adjusted demand (d_adj), not raw outflow, because raw outflow
  understates stocked-out depots and overstates overstocked ones - the case's root cause.
* All aggregates (region, national, lead-time totals, season) are sums of PATHS. Quantiles are taken last.
* P50s are not required to add. If a planner view scales depot P50s to the region P50 it is tagged RECONCILED."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..settings import Params
from . import events as EV
from . import tags_local as TL
from .combine import combine_totals, nested_rescale
from .common import rng_for, summarise
from .pipeline import EngineRun
from .uncertainty import EVENT_SCENARIO, SCENARIO, scenario_sigma

CHANNELS = ("GT", "MT", "QC")


# ---------------------------------------------------------------------------------------------------------
# depot shares
# ---------------------------------------------------------------------------------------------------------
@dataclass
class ShareEstimate:
    depots: list[str]
    pi_hat: np.ndarray
    pi_prior: np.ndarray
    pi_obs: np.ndarray
    n_eff_weeks: float
    basis: str
    conc: float
    adjusted_share: float = 0.0        # fraction of the observed weight that rests on censor-adjusted points


def _prior_shares(run: EngineRun, flavor: str, depots: list[str], channel: str, month: pd.Timestamp) -> np.ndarray:
    """Last year's share of each depot (same flavor and channel, same month, d_adj basis). Equal if no history."""
    lm = run.launch_models.get(flavor)
    n = len(depots)
    if lm is not None:
        days = pd.date_range(run.day, periods=14)
        v = np.array([lm.prior_daily([d], channel, days).sum() for d in depots])
        return v / v.sum() if v.sum() > 0 else np.full(n, 1 / n)
    df = run.mf.df
    m0 = pd.Timestamp(year=month.year, month=month.month, day=1) - pd.DateOffset(years=1)
    sub = df[(df.flavor_id == flavor) & (df.channel_id == channel) & (df.month == m0) & df.depot_id.isin(depots)]
    if sub.empty:
        sub = df[(df.flavor_id == flavor) & (df.channel_id == channel) & df.depot_id.isin(depots)]
        if sub.empty:
            return np.full(n, 1 / n)
        sub = sub.groupby("depot_id", as_index=False)["rate_adj"].mean()
    v = np.array([float(sub[sub.depot_id == d]["rate_adj"].sum()) for d in depots])
    return v / v.sum() if v.sum() > 0 else np.full(n, 1 / n)


def depot_shares(run: EngineRun, flavor: str, region: str, kind: str) -> ShareEstimate:
    """kind: "QC" (daily depot QC feed) or "GTMT" (weekly depot dispatch). Spec M7."""
    p = run.params
    depots = run.scope_depots(region)
    n = len(depots)
    alpha_w = p["dirichlet_alpha_weeks"]
    half_days = 7.0 * p["share_half_life_weeks"]
    prior = _prior_shares(run, flavor, depots, "QC" if kind == "QC" else "GT", run.day)
    obs = np.zeros(n)
    wsum = 0.0
    adj_w = tot_w = 0.0
    if kind == "QC":
        q = run.snap.qc_daily
        q = q[(q.flavor_id == flavor) & q.depot_id.isin(depots) & (q.date >= run.day - pd.Timedelta(days=182))]
        if len(q):
            piv = q.pivot_table(index="date", columns="depot_id", values="units_sold", aggfunc="sum").reindex(columns=depots)
            flg = q.pivot_table(index="date", columns="depot_id", values="stockout_flag", aggfunc="max").reindex(columns=depots).fillna(0)
            clean = piv.where(flg == 0)
            runrate = clean.rolling(14, min_periods=3).mean().shift(1).ffill()
            dadj = piv.where(flg == 0, np.maximum(piv, runrate.fillna(piv)))      # CENSOR_ADJUSTED, shares only
            age = (run.day - dadj.index).days.to_numpy().astype(float)
            w = 0.5 ** (age / half_days)
            obs = (dadj.fillna(0).to_numpy() * w[:, None]).sum(axis=0)
            wsum = float(w.sum())
            adj_w = float(((flg == 1).to_numpy() * w[:, None] * piv.fillna(0).to_numpy()).sum())
            tot_w = float((w[:, None] * piv.fillna(0).to_numpy()).sum())
    else:
        wk = run.weekly
        if len(wk):
            sub = wk[(wk.flavor_id == flavor) & wk.depot_id.isin(depots) & (wk.week_start >= run.day - pd.Timedelta(days=182))].copy()
            if len(sub):
                sub["obs"] = sub["disp_GT"] + sub["disp_MT"]
                exp = {d: None for d in depots}
                vals = []
                for d in depots:
                    s = sub[sub.depot_id == d].sort_values("week_start")
                    e = run.expected_weekly(s, flavor, d) if len(s) else np.array([])
                    s = s.assign(exp=e)
                    s["dadj"] = np.where(s["censored"], np.maximum(s["obs"], np.nan_to_num(s["exp"], nan=0.0)), s["obs"])
                    vals.append(s)
                allw = pd.concat(vals)
                age = (run.day - allw["week_start"]).dt.days.to_numpy().astype(float)
                allw["w"] = 0.5 ** (age / half_days)
                obs = np.array([float((allw[allw.depot_id == d]["dadj"] * allw[allw.depot_id == d]["w"]).sum()) for d in depots])
                wsum = float(allw[allw.depot_id == depots[0]]["w"].sum() * 7.0)
                adj_w = float((allw["censored"] * allw["w"] * allw["obs"]).sum())
                tot_w = float((allw["w"] * allw["obs"]).sum())
    pi_obs = obs / obs.sum() if obs.sum() > 0 else prior.copy()
    n_eff = wsum / 7.0
    pi_hat = (alpha_w * prior + n_eff * pi_obs) / (alpha_w + n_eff)
    pi_hat = pi_hat / pi_hat.sum()
    conc = float(min(p["dirichlet_conc_cap"], 4.0 * n_eff + alpha_w))
    return ShareEstimate(depots, pi_hat, prior, pi_obs, n_eff, "QC_DAILY_DADJ" if kind == "QC" else "DISPATCH_DADJ",
                         conc, adj_w / tot_w if tot_w > 0 else 0.0)


def dirichlet_draw(share: ShareEstimate, n: int, rng: np.random.Generator) -> np.ndarray:
    """n x depots; each row sums to 1 exactly."""
    a = np.maximum(share.conc * share.pi_hat, 1e-3)
    g = rng.gamma(a, 1.0, size=(n, len(a)))
    return g / g.sum(axis=1, keepdims=True)


# ---------------------------------------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------------------------------------
@dataclass
class PathSet:
    dates: pd.DatetimeIndex
    by_channel: dict[str, np.ndarray]               # n x days
    total: np.ndarray                               # n x days
    interval_type: str
    warnings: list[str] = field(default_factory=list)
    grain: dict[str, str] = field(default_factory=dict)
    detail: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return self.total.shape[0]

    def totals(self, days: int, channel: str | None = None) -> np.ndarray:
        a = self.total if channel in (None, "ALL") else self.by_channel[channel]
        return a[:, :days].sum(axis=1)


def _common_uniform(run: EngineRun, flavor: str, n: int) -> np.ndarray:
    return rng_for(run.day, flavor, "scenario-uniform").random(n)


def _weekly_level_error(det, H: int) -> float:
    """How far the baseline has been off over a whole week, measured on past in-control weeks only (7-day blocks of
    sold / baseline, all seven days uncensored and no open event). A level miss persists over the horizon, so it grows
    a little with H. Zero until at least four such weeks exist."""
    if det is None or det.df.empty:
        return 0.0
    df = det.df.tail(140)
    ok = (~df["censored"].to_numpy()) & (df["state"].to_numpy() == "NORMAL") & np.isfinite(df["R"].to_numpy()) & (df["R"].to_numpy() > 0)
    lr = np.where(ok, np.log(np.maximum(df["R"].to_numpy(), 1e-9)), np.nan)
    blocks = []
    for k in range(len(lr) // 7):
        b = lr[len(lr) - 7 * (k + 1): len(lr) - 7 * k]
        if np.isfinite(b).all():
            blocks.append(float(np.mean(b)))
    if len(blocks) < 4:
        return 0.0
    sd = float(np.sqrt(np.mean(np.square(blocks))))                 # root mean square: includes a steady bias
    return sd * float(np.sqrt(1.0 + min(max(H / 7.0 - 1.0, 0.0), 3.0) * 0.3))


def region_paths(run: EngineRun, flavor: str, region: str, dates: pd.DatetimeIndex, n: int, rho_passthrough: float,
                 sigma_horizon_days: float | None = None, no_event: bool = False) -> dict:
    """Region-level channel paths (n x days) before the depot split, plus tags."""
    p = run.params
    H = len(dates)
    rho_c = p["common_shock_rho"]
    horizon = sigma_horizon_days or H
    sig = scenario_sigma(horizon, "region", p)
    ec = rng_for(run.day, flavor, "common-shock").standard_normal(n)
    out: dict[str, np.ndarray] = {}
    warnings: list[str] = []
    interval = SCENARIO
    detail: dict = {}

    # ---- QC: daily layer (M3/M4) combined with the monthly layer (M5) ---------------------------------
    det = run.detection(flavor, region)
    base = run.qc_baseline_future(flavor, region, dates)
    ei = None
    if det is not None and not det.df.empty and not no_event:
        ei = EV.event_impact(det, p, np.pad(base, (0, max(0, 28 - H)), mode="edge"), rng_for(run.day, flavor, region, "event"),
                             n=n, H=max(H, 28), u=_common_uniform(run, flavor, n))
    sigma_e = max(float(det.sigma_e), 0.03) if det is not None else 0.1
    en_q = rng_for(run.day, flavor, region, "QC", "node").standard_normal(n)
    noise = np.exp(sigma_e * rng_for(run.day, flavor, region, "QC", "daily").standard_normal((n, H)) - sigma_e ** 2 / 2)
    level_sd = sigma_e * np.sqrt(p["qc_level_gain"] / (2 - p["qc_level_gain"]))
    level_sd = float(np.sqrt(level_sd ** 2 + _weekly_level_error(det, H) ** 2))
    en_mix = np.sqrt(rho_c) * ec + np.sqrt(1 - rho_c) * en_q            # a level miss is shared across regions
    level = np.exp(level_sd * en_mix - level_sd ** 2 / 2)[:, None]
    if ei is not None:
        qc = ei.daily_paths[:n, :H]
        interval = EVENT_SCENARIO
        detail["event"] = {"direction": ei.direction, "R_p": ei.R_p, "weights": ei.weights.tolist(), "shape_unknown": ei.shape_unknown}
        if ei.shape_unknown:
            warnings.append("EVENT_SHAPE_UNKNOWN")
        Rpath = ei.R_paths[:n, :H]
    else:
        qc = base[None, :] * level * noise
        Rpath = np.ones((n, H))
    # monthly layer for the QC region node, phased per day
    mdaily = np.zeros(max(H, 28))
    long_dates = pd.date_range(dates[0], periods=max(H, 28))
    for i, d in enumerate(long_dates):
        node = run.monthly_node(flavor, region, "QC", d)
        mdaily[i] = (node["p50"] / d.days_in_month) if node else float(base[min(i, H - 1)])
    if H >= 7:
        pad = np.pad(qc, ((0, 0), (0, max(0, 28 - H))), mode="edge") if H < 28 else qc
        T, diag = combine_totals(pad, mdaily, "region", ei is not None, p)
        scaled, factors = nested_rescale(pad, T)
        qc = scaled[:, :H]
        detail["combine"] = {"T": T, "factors": {f"{a}-{b}": f for (a, b), f in factors.items()}, "diag": diag}
    out["QC"] = qc

    # ---- GT / MT: phased monthly totals ---------------------------------------------------------------
    for ch in ("GT", "MT"):
        daily = np.zeros(H)
        method = "NONE"
        for i, d in enumerate(dates):
            node = run.monthly_node(flavor, region, ch, d)
            if node is None:
                continue
            method = node["method"]
            daily[i] = node["p50"] / d.days_in_month
        if (daily == 0).all():
            warnings.append(f"NO_MONTHLY_BASELINE_{ch}")
        en = rng_for(run.day, flavor, region, ch, "node").standard_normal(n)
        lvl = np.exp(sig * (np.sqrt(rho_c) * ec + np.sqrt(1 - rho_c) * en))[:, None]
        dn = 0.05
        dn_noise = np.exp(dn * rng_for(run.day, flavor, region, ch, "daily").standard_normal((n, H)) - dn ** 2 / 2)
        arr = daily[None, :] * lvl * dn_noise
        if rho_passthrough > 0 and ei is not None:
            arr = arr * (1.0 + rho_passthrough * (Rpath - 1.0))              # rho x (R - 1) x GT/MT baseline, ASSUMED or ESTIMATED
        out[ch] = arr
        detail.setdefault("monthly_method", {})[ch] = method
    if flavor in run.launch_models:
        warnings.append("EXTRAPOLATING")
    if det is not None and (det.series.signal == "UNITS_SOLD_SIGNAL"):
        warnings.append("UNITS_SOLD_SIGNAL")
    return {"paths": out, "interval": interval, "warnings": warnings, "detail": detail}


def build_paths(run: EngineRun, flavor: str, depot: str, days: int = 60, n: int | None = None, surge: float = 1.0,
                rho: float | None = None, start: pd.Timestamp | None = None, no_event: bool = False) -> PathSet:
    """Daily demand sample paths for one depot, by channel. Latent demand: not capped by stock or capacity."""
    p = run.params
    n = n or p["n_paths"]
    region = run.ds.depot_region()[depot]
    start = pd.Timestamp(start) if start is not None else run.day
    dates = pd.date_range(start, periods=days)
    rho_pt = float(p["pass_through_rho"] if rho is None else rho)
    cache = run.__dict__.setdefault("_rcache", {})
    rkey = (flavor, region, str(start.date()), days, n, round(rho_pt, 4), no_event)
    if rkey not in cache:
        cache[rkey] = region_paths(run, flavor, region, dates, n, rho_pt, sigma_horizon_days=days, no_event=no_event)
    rp = cache[rkey]
    by: dict[str, np.ndarray] = {}
    detail = dict(rp["detail"])
    for ch in CHANNELS:
        kind = "QC" if ch == "QC" else "GTMT"
        skey = ("share", flavor, region, kind, n)
        if skey not in cache:
            sh0 = depot_shares(run, flavor, region, kind)
            cache[skey] = (sh0, dirichlet_draw(sh0, n, rng_for(run.day, flavor, region, kind, "dirichlet")))
        sh, draw = cache[skey]
        col = sh.depots.index(depot)
        by[ch] = rp["paths"][ch] * draw[:, [col]] * surge
        detail.setdefault("shares", {})[ch] = {"depots": sh.depots, "pi_hat": sh.pi_hat.tolist(), "pi_prior": sh.pi_prior.tolist(),
                                              "pi_obs": sh.pi_obs.tolist(), "n_eff_weeks": sh.n_eff_weeks, "basis": sh.basis,
                                              "adjusted_share": sh.adjusted_share}
    total = sum(by.values())
    warn = list(rp["warnings"])
    if surge != 1.0:
        warn.append("SURGE_SCENARIO")
    return PathSet(dates, by, total, rp["interval"], warn,
                   {"GT": "PHASED", "MT": "PHASED", "QC": "OBSERVED_GRAIN"}, detail)


def region_total_paths(run: EngineRun, flavor: str, region: str, days: int, n: int, channel: str = "ALL") -> tuple[np.ndarray, PathSet]:
    """Region (or national when region == "ALL") totals as the sum of region path sets, never of quantiles."""
    regions = run.ds.regions if region == "ALL" else [region]
    dates = pd.date_range(run.day, periods=days)
    tot = np.zeros((n, days))
    interval = SCENARIO
    warns: list[str] = []
    by = {c: np.zeros((n, days)) for c in CHANNELS}
    for r in regions:
        rp = region_paths(run, flavor, r, dates, n, run.params["pass_through_rho"], sigma_horizon_days=days)
        for c in CHANNELS:
            by[c] += rp["paths"][c]
        if rp["interval"] == EVENT_SCENARIO:
            interval = EVENT_SCENARIO
        warns += [w for w in rp["warnings"] if w not in warns]
    total = sum(by.values())
    arr = total if channel == "ALL" else by[channel]
    return arr, PathSet(dates, by, total, interval, warns, {"GT": "PHASED", "MT": "PHASED", "QC": "OBSERVED_GRAIN"}, {})
