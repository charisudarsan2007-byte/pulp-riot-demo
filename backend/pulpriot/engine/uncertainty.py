"""M8 uncertainty. Two named types for ordinary forecasts:

* SCENARIO   - uncalibrated: sigma = scenario_sigma0 * sqrt(h_months) * grain_factor(level), P10/P90 = p50 * exp(-/+ 1.2816 sigma)
* CONFORMAL  - only when the cell holds at least conformal_min_n pairs from the ENGINE's own backtest (never the
               company's archived forecasts, never censored actuals).
An open event switches the interval type to EVENT_SCENARIO (quantiles of the scenario mixture, see events.py).
The case's 30% error is a planning scale, not a +/-30% band: under normal error +/-30% covers only about 58%."""
from __future__ import annotations

import numpy as np

from ..settings import Params
from .common import Z10

SCENARIO, CONFORMAL, EVENT_SCENARIO = "SCENARIO", "CONFORMAL", "EVENT_SCENARIO"


def scenario_sigma(horizon_days: float, level: str, params: Params) -> float:
    """sigma0 * sqrt(h_months) * grain factor; h_months = horizon_days / 30.4."""
    gf = params["scenario_grain_factor"][level]
    return float(params["scenario_sigma0"] * np.sqrt(max(horizon_days, 1.0) / 30.4) * gf)


def scenario_interval(p50: float, sigma: float) -> tuple[float, float]:
    return p50 * float(np.exp(-Z10 * sigma)), p50 * float(np.exp(Z10 * sigma))


def coverage_of_pct_band(pct: float = 0.30, mape: float = 0.30) -> float:
    """Coverage of +/- pct under normal error with the given MAPE: 2 Phi(pct / sigma) - 1, sigma = mape / sqrt(2/pi)."""
    from scipy.stats import norm
    sigma = mape / np.sqrt(2 / np.pi)
    return float(2 * norm.cdf(pct / sigma) - 1)


def conformal_interval(p50: float, errors: np.ndarray, ages: np.ndarray, params: Params) -> tuple[float, float]:
    """e_i = log(y_i + kappa) - log(p50_i + kappa), weights 0.5^(age_i / half-life); weighted 10th and 90th percentiles."""
    kappa = params["kappa"]
    w = 0.5 ** (np.asarray(ages, dtype=float) / params["decay_half_life"])
    order = np.argsort(errors)
    e, ww = np.asarray(errors)[order], w[order]
    cw = np.cumsum(ww) / ww.sum()
    q10, q90 = e[np.searchsorted(cw, 0.10)], e[min(np.searchsorted(cw, 0.90), len(e) - 1)]
    return (p50 + kappa) * float(np.exp(q10)) - kappa, (p50 + kappa) * float(np.exp(q90)) - kappa


def interval_type_for(n_pairs: int, event_open: bool, params: Params) -> str:
    if event_open:
        return EVENT_SCENARIO
    return CONFORMAL if n_pairs >= params["conformal_min_n"] else SCENARIO
