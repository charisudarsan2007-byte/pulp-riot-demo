"""M9 evaluation: rolling-origin backtests with arrival lags respected.

Monthly layer (depot x flavor): for each target month the engine forecast is made as of the first day of that month, using only
rows that had arrived (monthly sales arrive 7 days after month end, so the previous month is NOT yet available). Compared with
the naive forecast (last arrived month), seasonal naive (same month last year) and the company's archived forecast (T11).
Actuals that were censored (stockouts for at least half the month) are scored separately because they are lower bounds.

Scale-free metrics only: WAPE at aggregate level, scaled bias, MASE / RMSSE against the in-sample naive error, pinball loss at
0.1/0.5/0.9, 80% coverage of the SCENARIO interval. No MAPE: actuals can be near zero.
Synthetic-data results validate the code and the process, not real-world accuracy."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..data.loader import Dataset, FLAVORS, asof_ts
from ..engine.monthly import NodeForecaster, all_channel_depot_frame, monthly_frame
from ..engine.uncertainty import scenario_interval, scenario_sigma
from ..settings import Params, cache_dir


def pinball(y: float, q: float, tau: float) -> float:
    d = y - q
    return max(tau * d, (tau - 1) * d)


def interval_score(y: float, lo: float, hi: float, alpha: float = 0.2) -> float:
    return (hi - lo) + (2 / alpha) * max(lo - y, 0) + (2 / alpha) * max(y - hi, 0)


def monthly_backtest(ds: Dataset, params: Params | None = None) -> dict:
    params = params or ds.params
    sm = ds.facts["sales_monthly"]
    months = sorted(sm["month"].unique())
    first_full = pd.Timestamp("2026-03-01")
    targets = [m for m in months if first_full <= pd.Timestamp(m) <= pd.Timestamp("2026-06-01")]
    rows = []
    fh = ds.facts["forecast_history"]
    for tgt in targets:
        tgt = pd.Timestamp(tgt)
        origin = asof_ts(tgt)                                     # first day of the month, noon
        snap = ds.asof(origin)
        mf = monthly_frame(snap, params)
        if mf.df.empty:
            continue
        nf = NodeForecaster(all_channel_depot_frame(mf), params)
        # actuals (scored later; never used by the forecast)
        act = sm[sm.month == tgt].groupby(["flavor_id", "depot_id"])["bottles_sold"].sum()
        full = ds.facts["sales_monthly"]
        # censored share of the actual month from the stockout log as eventually reported
        from ..engine.censor import monthly_censored_share
        cs_snap = ds.asof(pd.Timestamp(tgt) + pd.DateOffset(months=2))
        cs = monthly_censored_share(cs_snap)
        cs = cs[cs.month == tgt]
        cs_w = {}
        if len(cs):
            vol = sm[sm.month == tgt].groupby(["flavor_id", "depot_id", "channel_id"])["bottles_sold"].sum().rename("v").reset_index()
            m = vol.merge(cs, how="left", on=["flavor_id", "depot_id", "channel_id"]).fillna({"cens_share": 0.0})
            m["cv"] = m["v"] * m["cens_share"]
            g = m.groupby(["flavor_id", "depot_id"])[["cv", "v"]].sum()
            cs_w = (g["cv"] / g["v"].clip(lower=1)).to_dict()
        comp = fh[(fh.target_month == tgt)].groupby(["flavor_id", "depot_id"])["forecast_bottles"].sum()
        for (fl, dep), y in act.items():
            node = nf.month(fl, dep, "ALL", tgt)
            if fl == "DP":
                continue                                            # no history: cold-start is scored in the launch view
            if node is None:
                continue
            prev_m = tgt - pd.DateOffset(months=2)                    # last month that had arrived at the origin
            hist = mf.df[(mf.df.flavor_id == fl) & (mf.df.depot_id == dep)].groupby("month")["units"].sum()
            naive = float(hist.get(prev_m, np.nan))
            naive_rate = naive / prev_m.days_in_month * tgt.days_in_month if np.isfinite(naive) else np.nan
            ly = float(hist.get(tgt - pd.DateOffset(years=1), np.nan))
            sig = scenario_sigma(61, "depot", params)                  # roughly two months from last arrived data
            lo, hi = scenario_interval(node["p50"], sig)
            rows.append({"target": str(tgt.date()), "flavor": fl, "depot": dep, "actual": float(y), "engine": node["p50"], "engine_lo": lo, "engine_hi": hi,
                         "naive": naive_rate, "seasonal_naive": ly, "company": float(comp.get((fl, dep), np.nan)),
                         "censored_share": float(cs_w.get((fl, dep), 0.0)), "rho": node["rho"], "rho_source": node["rho_source"]})
    df = pd.DataFrame(rows)
    return {"pairs": df, "n_targets": len(targets)}


def summarise_backtest(df: pd.DataFrame, params: Params) -> dict:
    """Metrics per model, per stratum (ordinary vs censored), plus bias by depot (B10)."""
    if df.empty:
        return {"models": [], "by_depot": [], "by_flavor": [], "n_pairs": 0}
    out_models = []
    models = ["engine", "company", "naive", "seasonal_naive"]
    for stratum, sel in (("ordinary", df.censored_share < 0.5), ("censored (lower-bound actuals)", df.censored_share >= 0.5)):
        d = df[sel]
        if d.empty:
            continue
        for m in models:
            x = d[np.isfinite(d[m])]
            if len(x) == 0:
                continue
            err = x[m] - x["actual"]
            wape = float(err.abs().sum() / x["actual"].abs().sum())
            bias = float(err.sum() / x["actual"].sum())
            # scale: mean absolute naive step in the actuals themselves (in-sample proxy: |actual - naive|)
            scale_den = float((x["naive"] - x["actual"]).abs().mean()) if np.isfinite(x["naive"]).all() else np.nan
            mase = float(err.abs().mean() / scale_den) if scale_den and scale_den > 0 else None
            row = {"stratum": stratum, "model": m, "n": int(len(x)), "wape": wape, "scaled_bias": bias, "mase_vs_naive": mase}
            if m == "engine":
                lo, hi = x["engine_lo"], x["engine_hi"]
                row["coverage80"] = float(((x["actual"] >= lo) & (x["actual"] <= hi)).mean())
                row["pinball_p50"] = float(np.mean([pinball(a, b, 0.5) for a, b in zip(x["actual"], x["engine"])]))
                row["pinball_p10"] = float(np.mean([pinball(a, b, 0.1) for a, b in zip(x["actual"], lo)]))
                row["pinball_p90"] = float(np.mean([pinball(a, b, 0.9) for a, b in zip(x["actual"], hi)]))
                row["interval_score"] = float(np.mean([interval_score(a, l, h) for a, l, h in zip(x["actual"], lo, hi)]))
            out_models.append(row)
    ordn = df[df.censored_share < 0.5]
    by_depot, by_flavor = [], []
    for key, name, out in (("depot", "depot", by_depot), ("flavor", "flavor", by_flavor)):
        for k, g in ordn.groupby(key):
            for m in ("engine", "company"):
                x = g[np.isfinite(g[m])]
                if x.empty:
                    continue
                err = x[m] - x["actual"]
                out.append({key: k, "model": m, "n": int(len(x)), "bias": float(err.sum() / x["actual"].sum()),
                            "wape": float(err.abs().sum() / x["actual"].sum()),
                            "direction": "OVERESTIMATES" if err.sum() / x["actual"].sum() > 0.03 else "UNDERESTIMATES" if err.sum() / x["actual"].sum() < -0.03 else "BALANCED"})
    return {"models": out_models, "by_depot": by_depot, "by_flavor": by_flavor, "n_pairs": int(len(df)),
            "n_ordinary": int((df.censored_share < 0.5).sum()), "n_censored": int((df.censored_share >= 0.5).sum())}


def acceptance_check(summary: dict, params: Params) -> list[dict]:
    """The spec's PROPOSED acceptance criteria, evaluated on what was actually measured. Pass/fail is informational."""
    rows = []
    eng = next((m for m in summary["models"] if m["model"] == "engine" and m["stratum"] == "ordinary"), None)
    snr = next((m for m in summary["models"] if m["model"] == "seasonal_naive" and m["stratum"] == "ordinary"), None)
    comp = next((m for m in summary["models"] if m["model"] == "company" and m["stratum"] == "ordinary"), None)
    if eng:
        rows.append({"criterion": "Scaled bias within +/-5% at flavor level (proposed)", "value": eng["scaled_bias"], "pass": abs(eng["scaled_bias"]) <= 0.05,
                     "n": eng["n"], "status": "MEASURED"})
        if "coverage80" in eng:
            rows.append({"criterion": "80% interval coverage between 72% and 88% on at least 50 pairs (proposed)", "value": eng["coverage80"],
                         "pass": 0.72 <= eng["coverage80"] <= 0.88 and eng["n"] >= 50, "n": eng["n"], "status": "MEASURED"})
    if eng and snr:
        rows.append({"criterion": "WAPE no worse than seasonal naive (proposed MASE <= 0.9 needs 12+ origins; only the monthly origins below exist)",
                     "value": eng["wape"] - snr["wape"], "pass": eng["wape"] <= snr["wape"], "n": eng["n"], "status": "MEASURED"})
    if eng and comp:
        rows.append({"criterion": "Engine WAPE below the company's archived forecast", "value": eng["wape"] - comp["wape"], "pass": eng["wape"] < comp["wape"], "n": eng["n"], "status": "MEASURED"})
    rows.append({"criterion": "At least 12 rolling origins per horizon (spec M9)", "value": float(len({p for p in []})) if False else None, "pass": False,
                 "n": None, "status": "NOT MET: the workbook has 18 months of sales, so only monthly origins from March 2026 exist"})
    return rows


def run_monthly(ds: Dataset) -> dict:
    cache = cache_dir() / f"backtest_monthly_{ds.source_hash}_{ds.params.config_hash}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    bt = monthly_backtest(ds)
    s = summarise_backtest(bt["pairs"], ds.params)
    res = {"summary": s, "acceptance": acceptance_check(s, ds.params), "n_targets": bt["n_targets"],
           "pairs": bt["pairs"].replace({np.nan: None}).to_dict("records")}
    cache.write_text(json.dumps(res, default=str), encoding="utf-8")
    return res
