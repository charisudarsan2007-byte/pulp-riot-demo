"""Detection test by injection (spec M9): multiplicative ramps and drops are added to event-free QC series and the detector is
run again. Reports median and P90 delay to the first ALERT, miss rate, and false alarms per 100 series-days on the same windows
without injection. Also reports the same test with lag-1 autocorrelated noise added (rho 0.3), which raises false alarms.

Ramps: length 3, 7, 14, 21 days x peak 1.5, 2, 3, 4, 5, then hold. Drops: 0.8, 0.67, 0.5 x the same lengths. 25 repeats each.
Seeded: the same data and settings always give the same table. Results describe this synthetic feed only."""
from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pandas as pd

from ..data.loader import Dataset, asof_ts
from ..engine.events import run_detector
from ..engine.monthly import daily_seasonal, monthly_frame, seasonal_profile
from ..engine.qc_daily import QCSeries, build_qc_series
from ..settings import Params, cache_dir

LENGTHS = (3, 7, 14, 21)
PEAKS = (1.5, 2.0, 3.0, 4.0, 5.0)
DROPS = (0.8, 0.67, 0.5)
HOLD = 14
WINDOW_AFTER = 28


def _ramp(length: int, peak: float, total: int) -> np.ndarray:
    j = np.arange(1, total + 1)
    up = peak ** (np.minimum(j, length) / length)
    return up


def _event_free_windows(det_df: pd.DataFrame, start_lo: pd.Timestamp, start_hi: pd.Timestamp) -> list[int]:
    ok = []
    st = det_df["state"].to_numpy()
    cens = det_df["cens"].to_numpy() > 0
    dates = det_df["date"]
    for i in range(len(det_df) - WINDOW_AFTER - 1):
        d = dates.iloc[i]
        if d < start_lo or d > start_hi:
            continue
        lo = max(i - 10, 0)
        w = slice(lo, i + WINDOW_AFTER)
        if (st[w] == "NORMAL").all() and not cens[w].any():
            ok.append(i)
    return ok


def run_injection(ds: Dataset, params: Params | None = None, repeats: int = 25, seed: int = 26, acf: float = 0.0,
                  asof: str = "2026-08-31") -> dict:
    params = params or ds.params
    snap = ds.asof(asof_ts(asof))
    mf = monthly_frame(snap, params)
    series = build_qc_series(snap, ds.depot_region())
    rng = np.random.default_rng(seed)
    bases = []
    for (fl, reg), s in series.items():
        if fl == "DP" or reg == "ALL":
            continue
        g, _ = seasonal_profile(mf, fl, "QC", params)
        sd = daily_seasonal(g, s.dates)
        det = run_detector(s, sd, params)
        wins = _event_free_windows(det.df.reset_index(drop=True), pd.Timestamp("2026-03-12"), pd.Timestamp("2026-07-20"))
        if len(wins) >= 5:
            bases.append((s, sd, det.df.reset_index(drop=True), wins))
    rows = []
    fa_days = 0
    fa_events = 0

    def noise_ar(n: int) -> np.ndarray:
        e = rng.standard_normal(n) * 0.04
        x = np.zeros(n)
        for t in range(1, n):
            x[t] = acf * x[t - 1] + e[t]
        return x

    for kind, peaks in (("ramp", PEAKS), ("drop", DROPS)):
        for L in LENGTHS:
            for peak in peaks:
                delays, misses = [], 0
                for _ in range(repeats):
                    s, sd, dfb, wins = bases[rng.integers(len(bases))]
                    i0 = int(rng.choice(wins))
                    q = s.q.copy()
                    total = WINDOW_AFTER
                    mult = _ramp(L, peak, total)
                    idx = np.arange(i0, i0 + total)
                    qq = q.copy()
                    qq[idx] = q[idx] * mult
                    if acf > 0:
                        qq = qq * np.exp(noise_ar(len(qq)))
                    s2 = QCSeries(s.flavor, s.region, s.dates, qq, s.cens, s.depots)
                    det = run_detector(s2, sd, params)
                    d = det.df
                    d = d[d["date"] >= s.dates[i0]]
                    want = "ALERT"
                    hit = d[(d["tier"].isin(["ALERT", "CRITICAL"])) & (d["direction"].eq("UP") if kind == "ramp" else d["direction"].eq("DOWN"))] \
                        if "tier" in d else d.iloc[0:0]
                    if kind == "drop":
                        hit = d[d["tier"].isin(["ALERT", "CRITICAL"]) & (d["direction"] == "DOWN")]
                    if hit.empty:
                        misses += 1
                    else:
                        delays.append(int((hit.iloc[0]["available_on"] - s.dates[i0]).days))
                rows.append({"kind": kind, "length_days": L, "peak": peak, "n": repeats, "median_delay_days": float(np.median(delays)) if delays else None,
                             "p90_delay_days": float(np.quantile(delays, .9)) if delays else None, "miss_rate": misses / repeats})
    # false alarms on the same windows without injection
    for s, sd, dfb, wins in bases:
        det = run_detector(s, sd, params)
        d = det.df
        win = d[(d["date"] >= pd.Timestamp("2026-03-12")) & (d["date"] <= pd.Timestamp("2026-07-20"))]
        fa_days += len(win)
        fa_events += int(((win["state"] == "ONSET") & (win["state"].shift(1) != "ONSET")).sum())
    far = fa_events / max(fa_days, 1) * 100
    return {"asof": asof, "acf": acf, "rows": rows, "false_alarms_per_100_series_days": far, "false_alarm_events": fa_events,
            "series": len(bases), "series_days": fa_days, "summer_equivalent_events_20_series_122_days": fa_events / max(fa_days, 1) * 20 * 122,
            "params": {"cusum_h": params["cusum_h"], "cusum_k": params["cusum_k"], "cusum_h_watch": params["cusum_h_watch"],
                       "sigma_e_floor": params["sigma_e_floor"], "month_boundary_guard_days": params["month_boundary_guard_days"],
                       "event_min_ratio": params["event_min_ratio"]},
            "note": "Windows are event-free stretches of real series in the workbook (non-launch flavors). Noise comes from the data itself."}


def cached_injection(ds: Dataset, acf: float = 0.0) -> dict:
    p = cache_dir() / f"inject_{ds.source_hash}_{ds.params.config_hash}_{acf}.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    res = run_injection(ds, acf=acf)
    p.write_text(json.dumps(res, default=str), encoding="utf-8")
    return res
