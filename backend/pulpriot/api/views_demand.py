"""Demand views: multi-week forecast (B1), QC monitor (B3), spike alarm (B4), new-product predictor (B8), campaign calendar (B9),
accuracy scorecard (B10), plain-English explainer (B11)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..actions import store as ST
from ..data.loader import FLAVORS
from ..engine.forecast import get_forecast, node_paths, spike_rows
from ..engine.launch import build_hypothetical_launch, similarity
from ..eval import backtest as BT
from ..eval import inject as IJ
from ..supply import pulp as PU
from .service import Service
from .views_control import headline


def _weekly_actuals(run, flavor: str, depot: str | None, region: str | None, channel: str) -> pd.DataFrame:
    dsp = run.snap.dispatch_weekly
    d = dsp[dsp.flavor_id == flavor]
    if depot:
        d = d[d.depot_id == depot]
    elif region:
        dm = run.ds.depot_region()
        d = d[d.depot_id.map(dm) == region]
    if channel != "ALL":
        d = d[d.channel_id == channel]
    return d.groupby("week_start")["bottles_dispatched"].sum().reset_index().rename(columns={"bottles_dispatched": "actual"})


def forecast_view(svc: Service, day, flavor: str, depot: str | None = None, region: str | None = None, channel: str = "ALL",
                  reveal: bool = False) -> dict:
    run = svc.run(day)
    n = 600
    weeks = 8
    days = weeks * 7
    arr, ps = node_paths(run, flavor, channel, region, depot, days, n)
    wk_start = run.day
    rows = []
    for w in range(weeks):
        tot = arr[:, w * 7:(w + 1) * 7].sum(axis=1)
        rows.append({"week": w + 1, "start": str((wk_start + pd.Timedelta(days=7 * w)).date()), "p10": float(np.quantile(tot, .1)), "p50": float(np.quantile(tot, .5)),
                     "p90": float(np.quantile(tot, .9)), "mean": float(tot.mean())})
    horizons = []
    for h in ("7d", "14d", "28d"):
        r = get_forecast(run, flavor, channel, region, depot, h)[0]
        horizons.append({"horizon": h, "weeks": {"7d": 1, "14d": 2, "28d": 4}[h], **{k: r[k] for k in ("p10", "p50", "p90", "mean", "interval_type", "grain_tag", "warnings", "lower_bound_only")}})
    hist = _weekly_actuals(run, flavor, depot, region, channel)
    hist = hist[hist.week_start >= run.day - pd.Timedelta(days=7 * 20)]
    hist_rows = [{"week_start": str(r.week_start.date()), "actual": float(r.actual)} for r in hist.itertuples()]
    revealed = []
    if reveal:
        # replay-only overlay: sales that arrived after the as-of moment (never an input to the forecast)
        full = svc.ds.facts["dispatch_weekly"]
        f = full[full.flavor_id == flavor]
        if depot:
            f = f[f.depot_id == depot]
        elif region:
            f = f[f.depot_id.map(svc.ds.depot_region()) == region]
        if channel != "ALL":
            f = f[f.channel_id == channel]
        g = f.groupby("week_start")["bottles_dispatched"].sum()
        g = g[(g.index >= run.day) & (g.index <= run.day + pd.Timedelta(days=days))]
        revealed = [{"week_start": str(k.date()), "actual": float(v)} for k, v in g.items()]
    # fruit outlook 6-8 weeks (pulp kg = path-wise sum over flavors x kg per bottle)
    bom = PU.bom(run)
    outlook = []
    for ing, name in PU.ingredient_names(run).items():
        tot = np.zeros(n)
        for r in bom[bom.ingredient_id == ing].itertuples():
            a, _ = node_paths(run, r.flavor_id, "ALL", None, None, days, n)
            tot += a[:, :days].sum(axis=1) * r.kg_per_bottle
        outlook.append({"ingredient": ing, "name": name, "weeks": weeks, "p10": float(np.quantile(tot, .1)), "p50": float(np.quantile(tot, .5)),
                        "p90": float(np.quantile(tot, .9)), "unit": "kg"})
    price, margin = run.params["price"], run.params["margin"]
    return {"asof": str(run.day.date()), "flavor": flavor, "depot": depot, "region": region, "channel": channel, "weekly": rows, "horizons": horizons,
            "history": hist_rows, "revealed": revealed, "pulp_outlook": outlook, "price": price, "margin": margin,
            "interval_type": ps.interval_type, "warnings": ps.warnings,
            "grain": "QC is daily from data; GT and MT are monthly totals spread over calendar days (PHASED).",
            "legend": {"true": "Reported sales (OBSERVED, weekly depot dispatch)", "p50": "Middle forecast (P50)", "band": "Low (P10) to high (P90) forecast",
                       "revealed": "What happened next (replay only, never an input)"},
            "provenance": ["SYNTHETIC", "ESTIMATED"]}


def qc_monitor(svc: Service, day, flavor: str | None = None) -> dict:
    run = svc.run(day)
    names = svc.ds.flavor_name()
    rows = []
    for fl in ([flavor] if flavor else FLAVORS):
        s = run.series.get((fl, "ALL"))
        if s is None or len(s.q) < 8:
            continue
        dates = s.dates
        last = len(s.q) - 1
        q = s.q
        same_dow = [q[i] for i in range(last - 28, last, 7) if i >= 0]
        regs = []
        for reg in svc.ds.regions:
            sr = run.series.get((fl, reg))
            if sr is None:
                continue
            regs.append({"region": reg, "yesterday": float(sr.q[-1]), "avg7": float(np.nanmean(sr.q[-8:-1])), "censored": bool(sr.cens[-1] >= 0.5)})
        rows.append({"flavor": fl, "name": names[fl], "date": str(dates[-1].date()), "yesterday": float(q[last]), "avg7": float(np.nanmean(q[last - 7:last])),
                     "avg_same_weekday_4w": float(np.mean(same_dow)) if same_dow else None, "ratio_vs_avg7": float(q[last] / max(np.nanmean(q[last - 7:last]), 1)),
                     "censored": bool(s.cens[last] >= 0.5), "regions": regs,
                     "series": [{"date": str(d.date()), "q": float(v), "censored": bool(c >= 0.5)} for d, v, c in zip(dates[-45:], q[-45:], s.cens[-45:])]})
    return {"asof": str(run.day.date()), "rows": rows, "signal": "units sold, quick-commerce depots; stockout-flagged days are lower bounds",
            "platform_split": "The supplied feed has no platform split, so Blinkit, Zepto and Swiggy are not shown separately.",
            "provenance": ["SYNTHETIC", "OBSERVED"], "freshness": "previous day (feed arrives next morning)"}


def spike_view(svc: Service, day, flavor: str | None = None, rho: float | None = None) -> dict:
    run = svc.with_rho(day, rho)
    names = svc.ds.flavor_name()
    rows = spike_rows(run, flavor)
    price, margin = run.params["price"], run.params["margin"]
    for r in rows:
        r["flavor_name"] = names[r["flavor"]]
        if r.get("extra_bottles"):
            r["money"] = {h: {"revenue": {k: v * price for k, v in e.items()}, "margin": {k: v * price * margin for k, v in e.items()}}
                          for h, e in r["extra_bottles"].items()}
    nat = [r for r in rows if r["region"] == "ALL"]
    top = sorted([r for r in nat if r["stage"] not in ("NORMAL", "REVERTED")], key=lambda r: -abs(np.log(max(r["ratio_R"], 1e-3))))
    sel = flavor or (top[0]["flavor"] if top else "DP")
    tiers = {"WATCH": "1.5x baseline (down: 1/1.5)", "ALERT": "2x, or an event is open", "CRITICAL": "3x"}
    return {"asof": str(run.day.date()), "rows": rows, "selected": sel, "tiers": tiers, "pass_through": {"rho": run.params["pass_through_rho"], "source": "ASSUMED (slider, default 0)",
            "estimate": pass_through(svc, day)}, "price": price, "margin": margin, "provenance": ["SYNTHETIC", "ESTIMATED"]}


def pass_through(svc: Service, day) -> dict:
    """QC -> GT/MT pass-through estimate (M4b). Needs 8 overlapping uncensored weeks; reports why when it cannot run."""
    run = svc.run(day)
    from ..engine.depletion import estimate_pass_through
    fl = "DP"
    det = {r: run.detection(fl, r) for r in svc.ds.regions}
    wk = run.weekly
    rows = []
    lm = run.launch_models.get(fl)
    if lm is not None and len(wk):
        for r, d in det.items():
            if d is None or d.df.empty:
                continue
            df = d.df
            for w, g in df.groupby(df["date"].dt.to_period("W-SUN")):
                start = g["date"].min() - pd.Timedelta(days=g["date"].min().weekday())
                unc = g[g["cens"] < 0.5]
                if len(unc) < 5:
                    continue
                depots = run.scope_depots(r)
                sub = wk[(wk.flavor_id == fl) & wk.depot_id.isin(depots) & (wk.week_start == start)]
                if sub.empty or sub["censored"].any():
                    continue
                y = float((sub["disp_GT"] + sub["disp_MT"]).sum())
                exp = 0.0
                for ch in ("GT", "MT"):
                    exp += float(lm.prior_daily(depots, ch, pd.date_range(start, periods=7)).sum() * np.exp(run.launch_shift(fl, ch)[0]))
                if y > 0 and exp > 0:
                    rows.append({"region": r, "week": start, "y_ratio_log": float(np.log(y / exp)), "logR": float(np.log(max(unc["R"].mean(), 1e-3)))})
    est = estimate_pass_through(pd.DataFrame(rows))
    est["weeks_required"] = run.params["pass_through_min_weeks"]
    est["applied"] = bool(est.get("used"))
    est["fallback"] = "manual slider (default 0, ASSUMED)" if not est.get("used") else "ESTIMATED"
    return est


def launch_view(svc: Service, day, flavor: str = "DP", similarity_override: dict | None = None, scale: float = 1.0,
                hypothetical: dict | None = None) -> dict:
    run = svc.run(day, similarity=similarity_override, scale=scale) if not hypothetical else svc.run(day)
    names = svc.ds.flavor_name()
    if hypothetical:
        ld = pd.Timestamp(hypothetical["launch_date"])
        lm = build_hypothetical_launch(svc.ds, run.snap, hypothetical["analogs"], ld, run.params, scale)
        fid = "NEW"
    else:
        lm = run.launch_models.get(flavor)
        fid = flavor
    if lm is None:
        return {"available": False, "reason": f"{names.get(flavor, flavor)} is not in its first six months at this date."}
    depots = svc.ds.depots
    horizon_w = 12
    days = pd.date_range(lm.launch_date, periods=7 * horizon_w)
    out_w = []
    sh = {}
    for c in ("GT", "MT", "QC"):
        sh[c] = run.launch_shift(flavor, c) if (not hypothetical and flavor in run.launch_models) else (0.0, 1.0 / np.sqrt(1.0 / lm.sigma_p ** 2))
    obs_w = {}
    if not hypothetical:
        dsp = run.snap.dispatch_weekly
        dsp = dsp[dsp.flavor_id == flavor]
        obs_w = dsp.groupby("week_start")["bottles_dispatched"].sum().to_dict()
    for w in range(horizon_w):
        d0 = lm.launch_date + pd.Timedelta(days=7 * w)
        dd = pd.date_range(d0, periods=7)
        prior = sum(float(lm.prior_daily(depots, c, dd).sum()) for c in ("GT", "MT", "QC"))
        shift = float(np.mean([sh[c][0] for c in ("GT", "MT", "QC")]))
        sd = float(np.mean([sh[c][1] for c in ("GT", "MT", "QC")]))
        sg = np.sqrt(sd ** 2)
        p50 = prior * np.exp(shift)
        out_w.append({"week": w + 1, "start": str(d0.date()), "prior": prior, "p50": p50, "p10": p50 * float(np.exp(-1.2816 * sg)), "p90": p50 * float(np.exp(1.2816 * sg)),
                      "observed": float(obs_w[d0]) if d0 in obs_w and d0 + pd.Timedelta(days=6) < run.day else None, "future": bool(d0 > run.day)})
    return {"available": True, "flavor": flavor if not hypothetical else "NEW", "name": names.get(flavor, "New flavor") if not hypothetical else hypothetical.get("name", "New flavor"),
            "launch_date": str(lm.launch_date.date()), "analogs": lm.analogs, "sigma_p": lm.sigma_p, "template": lm.template.tolist(), "weeks": out_w,
            "shift": {c: {"shift": sh[c][0], "sd_post": sh[c][1]} for c in sh}, "notes": lm.notes,
            "similarity_inputs": ["ingredient overlap", "price point", "channel mix", "launch season", "promo intensity"],
            "scale": scale, "interval_type": "SCENARIO", "warnings": ["EXTRAPOLATING"],
            "provenance": ["SYNTHETIC", "ASSUMED", "ESTIMATED"],
            "method": "Weekly curve = launch template (R03, ASSUMED) x similarity-weighted analog run-rate in the same season last year; shifted toward observed uncensored weeks."}


def campaigns_view(svc: Service, day) -> dict:
    run = svc.run(day)
    ev = svc.ds.refs["R06"]
    names = svc.ds.flavor_name()
    items = []
    for r in ev.itertuples():
        known_from = r.date
        if r.event_type == "launch":
            n_ = ev[ev.event_type == "launch notice"]
            if len(n_):
                known_from = n_.iloc[0]["date"]
        kind = r.event_type
        planned = kind in ("launch notice", "launch")
        items.append({"id": r.event_id, "date": str(r.date.date()), "kind": kind, "flavor": r.flavor_id if isinstance(r.flavor_id, str) and r.flavor_id in names else None,
                      "description": r.description, "known_from": str(pd.Timestamp(known_from).date()), "planned": planned,
                      "status": "upcoming" if pd.Timestamp(r.date) >= run.day else "past", "visible": pd.Timestamp(known_from) <= run.day,
                      "source": "R06_ref_events (SYNTHETIC)"})
    items = [i for i in items if i["visible"] and i["kind"] in ("launch notice", "launch", "influencer", "competitor", "route closure", "route reopen", "awareness (as-was)")]
    mine = [dict(c, source="Entered in this tool", status="upcoming" if pd.Timestamp(c["start_date"]) >= run.day else "past") for c in ST.list_campaigns()]
    return {"asof": str(run.day.date()), "events": items, "entered": mine,
            "note": "The workbook has no forward promotion calendar. Planned launches show from their notice date; anything marketing enters here appears as an unmodelled marker (no uplift is applied to the forecast).",
            "provenance": ["SYNTHETIC"]}


def accuracy_view(svc: Service) -> dict:
    res = svc.memo(("bt",), lambda: BT.run_monthly(svc.ds))
    inj = svc.job("inject", lambda: IJ.cached_injection(svc.ds, 0.0))
    inj2 = svc.job("inject_acf", lambda: IJ.cached_injection(svc.ds, 0.3))
    from ..engine.uncertainty import coverage_of_pct_band
    return {"summary": res["summary"], "acceptance": res["acceptance"], "pairs": res["pairs"], "n_targets": res["n_targets"],
            "injection": inj, "injection_acf": inj2, "coverage_of_30pct_band": coverage_of_pct_band(),
            "note": "Synthetic-data results validate the code and the process, not real-world accuracy. Re-run on company data before relying on them.",
            "provenance": ["SYNTHETIC", "MEASURED"]}


def explainer(svc: Service, day, flavor: str | None = None) -> dict:
    """B11: one plain line and the true-vs-predicted chart for the flavor chosen in B4."""
    run = svc.run(day)
    names = svc.ds.flavor_name()
    rows = spike_rows(run, flavor, region="ALL")
    if not flavor:
        live = [r for r in rows if r["stage"] not in ("NORMAL", "REVERTED")]
        flavor = sorted(live, key=lambda r: -abs(np.log(max(r["ratio_R"], 1e-3))))[0]["flavor"] if live else "DP"
        rows = spike_rows(run, flavor, region="ALL")
    s = rows[0] if rows else None
    line = headline(run, s, names) if s else {"text": f"{names[flavor]} is selling in line with its baseline."}
    from ..engine.hierarchy import region_total_paths
    arr, ps = region_total_paths(run, flavor, "ALL", 28, 600, "QC")
    fc = [{"date": str(d.date()), "p10": float(np.quantile(arr[:, j], .1)), "p50": float(np.quantile(arr[:, j], .5)), "p90": float(np.quantile(arr[:, j], .9))}
          for j, d in enumerate(ps.dates)]
    ser = run.series.get((flavor, "ALL"))
    hist = [] if ser is None else [{"date": str(d.date()), "q": float(v), "censored": bool(c >= 0.5)} for d, v, c in zip(ser.dates[-42:], ser.q[-42:], ser.cens[-42:])]
    det = run.detection(flavor, "ALL")
    base = []
    if det is not None and not det.df.empty:
        base = [{"date": str(r.date.date()), "baseline": float(r.baseline_units)} for r in det.df.tail(42).itertuples()]
    return {"asof": str(run.day.date()), "flavor": flavor, "name": names[flavor], "line": line, "history": hist, "baseline": base, "forecast": fc,
            "interval_type": ps.interval_type, "warnings": ps.warnings, "provenance": ["SYNTHETIC", "ESTIMATED"]}
