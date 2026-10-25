"""M2 monthly baseline.

Primary path in this workbook: seasonal-naive-growth fallback (spec M2 step 7), because no series has 24 months of
history (rule 8: under 24 months the fallback is primary and tagged FALLBACK_PRIMARY). The pooled local-level model
(steps 1-5) is implemented in full and used automatically once a series has 24 or more months.

Departure from the spec text, stated once: seasonal profiles use the daily rate (units / days in month) so that
February is not mistaken for a seasonal dip. Everything else follows the spec."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..data.loader import Snapshot
from ..settings import Params
from .censor import monthly_censored_share
from .common import add_months, lognormal_mean, month_start


@dataclass
class MonthlyFrame:
    df: pd.DataFrame            # month, flavor_id, depot_id, channel_id, units, days, rate, cens_share, units_adj, adjusted
    last_month: pd.Timestamp    # latest month that has arrived
    n_months: int


def monthly_frame(snap: Snapshot, params: Params) -> MonthlyFrame:
    sm = snap.sales_monthly
    if sm.empty:
        return MonthlyFrame(pd.DataFrame(), pd.NaT, 0)
    df = sm.rename(columns={"bottles_sold": "units"})[["month", "flavor_id", "depot_id", "channel_id", "units"]].copy()
    df["days"] = df["month"].dt.days_in_month
    cens = monthly_censored_share(snap)
    df = df.merge(cens, how="left", on=["month", "flavor_id", "depot_id", "channel_id"])
    df["cens_share"] = df["cens_share"].fillna(0.0)
    df["rate"] = df["units"] / df["days"]
    df = df.sort_values(["flavor_id", "depot_id", "channel_id", "month"]).reset_index(drop=True)
    df = _adjust(df)
    return MonthlyFrame(df, df["month"].max(), df["month"].nunique())


def _adjust(df: pd.DataFrame) -> pd.DataFrame:
    """d_adj for censored months (cens_share >= 0.5): max(observed, prior-year same month). A point estimate used
    for growth ratios and shares only. Without a prior-year month the observation stays a lower bound."""
    df = df.copy()
    key = ["flavor_id", "depot_id", "channel_id"]
    prior = df[key + ["month", "rate"]].copy()
    prior["month"] = prior["month"] + pd.DateOffset(years=1)
    df = df.merge(prior.rename(columns={"rate": "rate_prior"}), how="left", on=key + ["month"])
    cens = df["cens_share"] >= 0.5
    df["rate_adj"] = df["rate"]
    adj_rate = np.maximum(df["rate"], df["rate_prior"].fillna(df["rate"]))
    df.loc[cens, "rate_adj"] = adj_rate[cens]
    df["adjusted"] = cens & df["rate_prior"].notna() & (df["rate_adj"] > df["rate"])
    df["lower_bound"] = cens & df["rate_prior"].isna()
    df["units_adj"] = df["rate_adj"] * df["days"]
    return df


# -------------------------------------------------------------------------------------------------------
# seasonal profile (log daily rate, centred), pooled by group with a mask on censored months
# -------------------------------------------------------------------------------------------------------
def _profile_from(df: pd.DataFrame, kappa: float, mask: bool) -> np.ndarray:
    """Mean over series and years of (z - series mean) per month-of-year; NaN where no clean observation."""
    if df.empty:
        return np.full(12, np.nan)
    d = df.copy()
    d["z"] = np.log(d["rate"] + kappa)
    clean = d[d["cens_share"] < 0.5] if mask else d
    if clean.empty:
        return np.full(12, np.nan)
    clean = clean.assign(dev=clean["z"] - clean.groupby(["flavor_id", "depot_id", "channel_id"])["z"].transform("mean"))
    clean = clean.assign(moy=clean["month"].dt.month)
    prof = clean.groupby("moy")["dev"].mean().reindex(range(1, 13))
    return prof.to_numpy()


def seasonal_profile(mf: MonthlyFrame, flavor: str, channel: str, params: Params) -> tuple[np.ndarray, str]:
    """gamma[g, j] for j = 1..12 (index 0..11), centred to sum zero. Returns (gamma, level used).

    Fallback chain per month-of-year: (flavor, channel) -> (flavor, ALL) -> portfolio."""
    kappa = params["kappa"]
    mask = params["seasonal_mask_censored"]
    df = mf.df
    if df.empty:
        return np.zeros(12), "NONE"
    own = _profile_from(df[(df.flavor_id == flavor) & (df.channel_id == channel)], kappa, mask)
    fl = _profile_from(df[df.flavor_id == flavor], kappa, mask)
    port = _profile_from(df, kappa, mask)
    out = np.where(np.isnan(own), np.where(np.isnan(fl), port, fl), own)
    level = "GROUP" if not np.isnan(own).any() else ("FLAVOR" if not np.isnan(fl).any() else "PORTFOLIO")
    out = np.where(np.isnan(out), 0.0, out)
    return out - out.mean(), level


def daily_seasonal(gamma: np.ndarray, dates: pd.DatetimeIndex, mode: str = "step") -> np.ndarray:
    """Daily seasonal component from the monthly gamma.

    mode="interp": linear interpolation between month midpoints (spec M3 text).
    mode="step":   month-constant, the same phasing as GT/MT monthly totals. Default here, because the supplied QC feed
                   moves in month-sized steps (monthly volumes are the planning grain) and interpolation leaves a
                   persistent residual at every month boundary. Both are covered by tests."""
    if mode == "step":
        return np.array([gamma[m - 1] for m in dates.month])
    mids = []
    vals = []
    for yr in range(dates.min().year - 1, dates.max().year + 2):
        for m in range(1, 13):
            first = pd.Timestamp(year=yr, month=m, day=1)
            mids.append((first + pd.Timedelta(days=first.days_in_month / 2.0)).value)
            vals.append(gamma[m - 1])
    return np.interp(dates.values.astype("int64"), np.array(mids), np.array(vals))


# -------------------------------------------------------------------------------------------------------
# fallback benchmark and the primary path here: seasonal_naive_growth
# -------------------------------------------------------------------------------------------------------
def _rate_lookup(mf: MonthlyFrame) -> dict:
    d = mf.df
    return {(r.flavor_id, r.depot_id, r.channel_id, r.month): r.rate_adj for r in d.itertuples()}


def growth_ratio(mf: MonthlyFrame, series: tuple, group: list[tuple], lookup: dict) -> tuple[float, str]:
    """rho = (3 rho_s + 3 rho_g) / 6 from the last three arrived months against the same months a year earlier."""
    last = mf.last_month
    if pd.isna(last):
        return 1.0, "NO_GROWTH_HISTORY"
    months = [add_months(last, -i) for i in range(3)]

    def ratio(keys: list[tuple]) -> float | None:
        """Sum over the months that have both years; at least one paired month is needed."""
        num = den = 0.0
        pairs = 0
        for m in months:
            a_tot = b_tot = 0.0
            ok = True
            for k in keys:
                a = lookup.get((*k, m))
                b = lookup.get((*k, m - pd.DateOffset(years=1)))
                if a is None or b is None:
                    ok = False
                    break
                a_tot += a
                b_tot += b
            if ok:
                num += a_tot
                den += b_tot
                pairs += 1
        return num / den if pairs and den > 0 else None

    rs, rg = ratio([series]), ratio(group)
    if rs is None and rg is None:
        return 1.0, "NO_GROWTH_HISTORY"
    if rs is None:
        rho = rg
    elif rg is None:
        rho = rs
    else:
        rho = (3 * rs + 3 * rg) / 6
    return float(np.clip(rho, 0.5, 2.0)), "ESTIMATED"


def seasonal_naive_growth(mf: MonthlyFrame, series: tuple, target: pd.Timestamp, lookup: dict | None = None,
                          group: list[tuple] | None = None) -> dict | None:
    """Forecast monthly units for `target` month: y[m-12] * rho (h <= 12) or y[m-24] * rho^2 (h > 12)."""
    lookup = lookup if lookup is not None else _rate_lookup(mf)
    group = group or [series]
    target = month_start(target)
    rho, rho_src = growth_ratio(mf, series, group, lookup)
    y1 = lookup.get((*series, target - pd.DateOffset(years=1)))
    if y1 is not None:
        rate, years = y1 * rho, 1
    else:
        y2 = lookup.get((*series, target - pd.DateOffset(years=2)))
        if y2 is None:
            return None
        rate, years = y2 * rho ** 2, 2
    return {"p50": rate * target.days_in_month, "rho": rho, "rho_source": rho_src, "years_back": years,
            "method": "SEASONAL_NAIVE_GROWTH"}


# -------------------------------------------------------------------------------------------------------
# Pooled local-level model (spec M2 steps 1-5). Used when a series has >= 24 months.
# -------------------------------------------------------------------------------------------------------
def pooled_local_level(z: np.ndarray, gamma_by_month: np.ndarray, months: np.ndarray, censored_down: np.ndarray,
                       alpha_grid: list[float], burn_in: int = 6) -> tuple[float, float, np.ndarray]:
    """One group's smoothing: d = z - gamma; l[t] = l[t-1] + alpha (d[t] - l[t-1]); censored-down months skip the update.

    Returns (alpha, sigma_1, level path). alpha minimises the one-step absolute error after the burn-in."""
    d = z - gamma_by_month[months - 1]
    best = (None, np.inf, None, None)
    for a in alpha_grid:
        lvl = np.empty_like(d)
        lvl[0] = d[0]
        err = []
        for t in range(1, len(d)):
            e = d[t] - lvl[t - 1]
            if t >= burn_in and not censored_down[t]:
                err.append(abs(e))
            lvl[t] = lvl[t - 1] if censored_down[t] and e < 0 else lvl[t - 1] + a * e
        mae = float(np.mean(err)) if err else np.inf
        if mae < best[1]:
            best = (a, mae, lvl, np.std(np.diff(d)) if len(d) > 2 else 0.2)
    return float(best[0]), float(best[3]), best[2]


def pooled_forecast(level: float, gamma_target: float, sigma_1: float, h: int, kappa: float) -> dict:
    """Median = exp(l[T] + gamma) - kappa; sigma_h = sigma_1 sqrt(h); mean with the kappa-corrected formula."""
    med = float(np.exp(level + gamma_target) - kappa)
    sigma_h = sigma_1 * np.sqrt(h)
    return {"p50": med, "mean": lognormal_mean(med, sigma_h, kappa), "sigma_h": sigma_h, "method": "POOLED_LOCAL_LEVEL"}


# -------------------------------------------------------------------------------------------------------
def summer_share_check(mf: MonthlyFrame, lo: float = 0.45, hi: float = 0.51) -> dict:
    """Summer (Mar-Jun) share of annual units for the latest complete July-June year must be 0.48 +/- 0.03, else DATA_CONFLICT.

    The test needs a full July-June year of arrived months; before that it reports INSUFFICIENT_HISTORY (not a conflict)."""
    d = mf.df
    if d.empty:
        return {"status": "INSUFFICIENT_HISTORY", "share": None}
    months = set(d["month"].unique())
    last = mf.last_month
    end_year = last.year if last.month >= 6 else last.year - 1
    win = pd.date_range(pd.Timestamp(year=end_year - 1, month=7, day=1), periods=12, freq="MS")
    if not all(m in months for m in win):
        return {"status": "INSUFFICIENT_HISTORY", "share": None, "needs": "a full July-June year of arrived months"}
    w = d[d["month"].isin(win)]
    share = float(w[w.month.dt.month.isin([3, 4, 5, 6])].units.sum() / w.units.sum())
    return {"status": "OK" if lo <= share <= hi else "DATA_CONFLICT", "share": share, "year": f"{win[0].date()} to {win[-1].date()}"}


def history_months(mf: MonthlyFrame, series: tuple) -> int:
    d = mf.df
    return int(((d.flavor_id == series[0]) & (d.depot_id == series[1]) & (d.channel_id == series[2])).sum())


# -------------------------------------------------------------------------------------------------------
# Base nodes. Forecasts are made on aggregated series (summed first), never by adding depot medians.
# -------------------------------------------------------------------------------------------------------
def aggregate_frame(mf: MonthlyFrame, scope_of: dict[str, str] | None, add_all_channel: bool = True) -> MonthlyFrame:
    """Re-key the frame to a coarser scope. `scope_of` maps depot_id -> scope id (region id); None means one national
    scope "ALL". An extra channel_id == "ALL" series (all channels summed) is added. d_adj is summed with the units."""
    d = mf.df
    if d.empty:
        return mf
    x = d.copy()
    x["depot_id"] = x["depot_id"].map(scope_of) if scope_of else "ALL"
    x["cens_units"] = x["cens_share"] * x["units"]
    keys = ["month", "flavor_id", "depot_id", "channel_id"]
    g = x.groupby(keys, as_index=False).agg(units=("units", "sum"), units_adj=("units_adj", "sum"),
                                            cens_units=("cens_units", "sum"), days=("days", "first"),
                                            adjusted=("adjusted", "max"), lower_bound=("lower_bound", "max"))
    if add_all_channel:
        a = g.groupby(["month", "flavor_id", "depot_id"], as_index=False).agg(
            units=("units", "sum"), units_adj=("units_adj", "sum"), cens_units=("cens_units", "sum"),
            days=("days", "first"), adjusted=("adjusted", "max"), lower_bound=("lower_bound", "max"))
        a["channel_id"] = "ALL"
        g = pd.concat([g, a], ignore_index=True)
    g["cens_share"] = np.where(g["units"] > 0, g["cens_units"] / g["units"], 0.0)
    g["rate"] = g["units"] / g["days"]
    g["rate_adj"] = g["units_adj"] / g["days"]
    g = g.drop(columns=["cens_units"]).sort_values(["flavor_id", "depot_id", "channel_id", "month"]).reset_index(drop=True)
    return MonthlyFrame(g, mf.last_month, mf.n_months)


def all_channel_depot_frame(mf: MonthlyFrame) -> MonthlyFrame:
    """Depot-level frame with the extra ALL-channel series, for the weekly depletion expectation."""
    d = mf.df
    if d.empty:
        return mf
    ident = {dep: dep for dep in d["depot_id"].unique()}
    return aggregate_frame(mf, ident, True)


class NodeForecaster:
    """Seasonal-naive-growth (primary here) forecasts on one MonthlyFrame; lookups cached."""

    def __init__(self, mf: MonthlyFrame, params: Params):
        self.mf = mf
        self.params = params
        self.lookup = _rate_lookup(mf)
        d = mf.df
        self.groups: dict[tuple[str, str], list[tuple]] = {}
        for r in d[["flavor_id", "depot_id", "channel_id"]].drop_duplicates().itertuples(index=False):
            self.groups.setdefault((r.flavor_id, r.channel_id), []).append((r.flavor_id, r.depot_id, r.channel_id))
        self._cache: dict = {}

    def month(self, flavor: str, scope: str, channel: str, target: pd.Timestamp) -> dict | None:
        key = (flavor, scope, channel, month_start(target))
        if key not in self._cache:
            series = (flavor, scope, channel)
            self._cache[key] = seasonal_naive_growth(self.mf, series, target, self.lookup,
                                                     self.groups.get((flavor, channel), [series]))
        return self._cache[key]

    def history_months(self, flavor: str, scope: str, channel: str) -> int:
        return history_months(self.mf, (flavor, scope, channel))
