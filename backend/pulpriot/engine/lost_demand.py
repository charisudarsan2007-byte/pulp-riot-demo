"""Lost-demand estimate (spec M8 addendum).

For every censored QC day or censored depot week:  lost = forecast path - observed, floored at zero, summed by PATH over
the period, then P10/P50/P90. Uncensored periods contribute exactly zero. Revenue lost = lost x price.

Forecast used for a censored QC stretch: the event scenario mixture frozen at the last uncensored day (so a viral spike keeps
growing on paper while sales are capped), or the frozen baseline when no event was open. Observed sales are a lower bound
on demand, which is why the loss is a one-sided estimate."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from . import events as EV
from .common import rng_for, summarise
from .pipeline import EngineRun


def _stretches(mask: np.ndarray) -> list[tuple[int, int]]:
    out, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        if not m and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def lost_qc_paths(run: EngineRun, flavor: str, region: str, n: int = 1000, since: pd.Timestamp | None = None
                  ) -> tuple[np.ndarray, dict]:
    """(n-vector of lost bottles per path, diagnostics) for one QC series."""
    det = run.detection(flavor, region)
    if det is None or det.df.empty:
        return np.zeros(n), {"censored_days": 0}
    df = det.df.reset_index(drop=True)
    cens = (df["cens"].to_numpy() >= 0.5)
    if since is not None:
        cens &= (df["date"] >= since).to_numpy()
    total = np.zeros(n)
    days = 0
    u = rng_for(run.day, flavor, "scenario-uniform").random(n)
    for a, b in _stretches(cens):
        L = a - 1
        length = b - a + 1
        days += length
        base = df["baseline_units"].to_numpy()[a:b + 1]
        q = df["q"].to_numpy()[a:b + 1]
        rng = rng_for(run.day, flavor, region, "lost", a)
        F = None
        if L >= 0:
            trunc = replace(det, df=df.iloc[:L + 1])
            ei = EV.event_impact(trunc, run.params, np.pad(base, (0, max(0, 28 - length)), mode="edge"), rng, n=n,
                                 H=max(length, 28), u=u)
            if ei is not None and ei.direction == "UP":
                F = ei.daily_paths[:n, :length]
        if F is None:
            sg = max(float(det.sigma_e), 0.03)
            F = base[None, :] * np.exp(sg * rng.standard_normal((n, length)) - sg ** 2 / 2)
        total += np.maximum(F - q[None, :], 0.0).sum(axis=1)
    return total, {"censored_days": int(days)}


def lost_gtmt_paths(run: EngineRun, flavor: str, depot: str, n: int = 1000, since: pd.Timestamp | None = None
                    ) -> tuple[np.ndarray, dict]:
    """Censored depot weeks: phased GT+MT expectation (+ pass-through when active) minus GT+MT dispatch, floored at zero."""
    wk = run.weekly
    if wk.empty:
        return np.zeros(n), {"censored_weeks": 0}
    rows = wk[(wk.flavor_id == flavor) & (wk.depot_id == depot) & wk.censored]
    if since is not None:
        rows = rows[rows.week_start >= since]
    if rows.empty:
        return np.zeros(n), {"censored_weeks": 0}
    sig = 0.376 * np.sqrt(7 / 30.4)
    rng = rng_for(run.day, flavor, depot, "lost-gtmt")
    total = np.zeros(n)
    lm = run.launch_models.get(flavor)
    for r in rows.itertuples():
        days = pd.date_range(r.week_start, periods=7)
        exp = 0.0
        for ch in ("GT", "MT"):
            for (yr, mo), grp in pd.Series(days).groupby([days.year, days.month]):
                m0 = pd.Timestamp(year=yr, month=mo, day=1)
                if lm is not None:
                    exp += float(lm.prior_daily([depot], ch, pd.DatetimeIndex(grp.values)).sum() * np.exp(run.launch_shift(flavor, ch)[0]))
                else:
                    node = run.monthly_node(flavor, depot, ch, m0)
                    if node:
                        exp += node["p50"] / m0.days_in_month * len(grp)
        F = exp * np.exp(sig * rng.standard_normal(n) - sig ** 2 / 2)
        total += np.maximum(F - (r.disp_GT + r.disp_MT), 0.0)
    return total, {"censored_weeks": int(len(rows))}


def lost_demand(run: EngineRun, flavor: str | None = None, since: pd.Timestamp | None = None, n: int = 1000) -> list[dict]:
    """out_lost_demand rows by flavor and channel family (QC regions summed by path; GT/MT depots summed by path)."""
    price = run.params["price"]
    rows = []
    flavors = [flavor] if flavor else ["MPB", "GC", "PP", "DB", "DP"]
    for fl in flavors:
        qc = np.zeros(n)
        qdays = 0
        for reg in run.ds.regions:
            v, d = lost_qc_paths(run, fl, reg, n, since)
            qc += v
            qdays += d["censored_days"]
        gm = np.zeros(n)
        wks = 0
        for dep in run.ds.depots:
            v, d = lost_gtmt_paths(run, fl, dep, n, since)
            gm += v
            wks += d["censored_weeks"]
        for label, arr, cnt in (("QC", qc, qdays), ("GT+MT", gm, wks), ("ALL", qc + gm, qdays + wks)):
            s = summarise(arr)
            rows.append({"asof": str(run.day.date()), "flavor": fl, "channel": label, "period": "since " + str((since or pd.Timestamp("2026-01-01")).date()),
                         "lost_p10": s["p10"], "lost_p50": s["p50"], "lost_p90": s["p90"], "lost_mean": s["mean"],
                         "censored_days": int(cnt) if label == "QC" else None, "censored_weeks": int(cnt) if label == "GT+MT" else None,
                         "revenue_lost_p50": s["p50"] * price, "revenue_lost_p10": s["p10"] * price, "revenue_lost_p90": s["p90"] * price,
                         "provenance": ["ESTIMATED", "CENSOR_ADJUSTED"], "interval_type": "SCENARIO"})
    return rows
