"""Small shared helpers for the engine: log transform, quantile summaries, deterministic RNG."""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

Z10 = 1.2815515655446004   # N(0,1) 90th percentile


def zlog(y, kappa: float = 1.0):
    """z = log(y + kappa) so zeros are admissible."""
    return np.log(np.asarray(y, dtype=float) + kappa)


def zexp(z, kappa: float = 1.0):
    return np.exp(z) - kappa


def lognormal_mean(median: float, sigma: float, kappa: float = 1.0) -> float:
    """Spec M2 v1.1: mean = (median + kappa) * exp(sigma^2 / 2) - kappa."""
    return (median + kappa) * np.exp(sigma ** 2 / 2.0) - kappa


def summarise(paths: np.ndarray, axis: int = 0) -> dict[str, float]:
    """Quantiles of a vector of path totals. Aggregate paths first, take quantiles second."""
    v = np.asarray(paths, dtype=float)
    return {"p10": float(np.quantile(v, 0.10)), "p50": float(np.quantile(v, 0.50)),
            "p90": float(np.quantile(v, 0.90)), "mean": float(v.mean())}


def rng_for(*parts) -> np.random.Generator:
    """Deterministic generator keyed by arbitrary parts (as-of date, flavor, node...). Same inputs, same draws."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "little"))


def days_in_month(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).days_in_month)


def month_start(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return pd.Timestamp(year=t.year, month=t.month, day=1)


def add_months(ts, n: int) -> pd.Timestamp:
    return month_start(ts) + pd.DateOffset(months=n)


def robust_sigma(x: np.ndarray, floor: float) -> float:
    """1.4826 * MAD, floored."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 5:
        return floor
    med = np.median(x)
    return max(1.4826 * float(np.median(np.abs(x - med))), floor)
