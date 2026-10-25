"""M5 temporal combine for QC series: the daily layer (M3/M4 paths) and the monthly layer (M2) are merged in log space by
inverse variance for the 7, 14 and 28 day totals, then the daily paths are rescaled piecewise so that days add up across
horizons (sum of days 1-7 = T_7, days 1-14 = T_14, days 1-28 = T_28). The same factors are applied to every path, so
path sums stay coherent. GT and MT are not combined: they stay monthly totals spread over calendar days (PHASED)."""
from __future__ import annotations

import numpy as np

from ..settings import Params
from .uncertainty import scenario_sigma

HORIZONS = (7, 14, 28)


def combine_log(x_d: float, v_d: float, x_m: float, v_m: float) -> float:
    """Inverse-variance combination of two log totals."""
    return (x_d / v_d + x_m / v_m) / (1.0 / v_d + 1.0 / v_m)


def combine_totals(paths: np.ndarray, monthly_daily_p50: np.ndarray, level: str, event_open: bool, params: Params
                   ) -> tuple[dict[int, float], dict[int, dict]]:
    """Return combined totals T_H and diagnostics. `paths` is n x >=28 daily; `monthly_daily_p50` the monthly layer per day."""
    T, diag = {}, {}
    for H in HORIZONS:
        tot = paths[:, :H].sum(axis=1)
        x_d = float(np.log(max(np.median(tot), 1e-9)))
        v_d = max(float(np.var(np.log(np.maximum(tot, 1e-9)))), 0.05 ** 2)
        t_m = float(monthly_daily_p50[:H].sum())
        x_m = float(np.log(max(t_m, 1e-9)))
        v_m = scenario_sigma(H, level, params) ** 2
        if event_open:
            v_m *= 10.0
        T[H] = float(np.exp(combine_log(x_d, v_d, x_m, v_m)))
        diag[H] = {"daily_median": float(np.exp(x_d)), "monthly_total": t_m, "v_daily": v_d, "v_monthly": v_m}
    # nesting: T_14 >= T_7 and T_28 >= T_14
    T[14] = max(T[14], T[7])
    T[28] = max(T[28], T[14])
    return T, diag


def nested_rescale(paths: np.ndarray, T: dict[int, float]) -> tuple[np.ndarray, dict]:
    """Piecewise factors: days 1-7 -> T7, days 8-14 -> T14 - T7, days 15-28 -> T28 - T14 (median-path sums D)."""
    med = np.median(paths[:, :28], axis=0)
    segs = [(0, 7, T[7]), (7, 14, T[14] - T[7]), (14, 28, T[28] - T[14])]
    out = paths.copy()
    factors = {}
    for a, b, target in segs:
        d = float(med[a:b].sum())
        f = target / d if d > 0 else 1.0
        out[:, a:b] *= f
        factors[(a + 1, b)] = f
    if out.shape[1] > 28:                                # beyond four weeks the last factor continues
        out[:, 28:] *= factors[(15, 28)]
    return out, factors
