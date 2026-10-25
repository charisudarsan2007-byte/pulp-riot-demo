"""Function contract of the demand engine (spec section 7). Every output row carries provenance, interval type, grain tag,
data freshness and the model version. Inventory consumes lead-time totals and sample paths, never daily quantiles."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.loader import FLAVORS
from ..settings import MODEL_VERSION
from . import events as EV
from . import tags_local as TL
from .common import rng_for, summarise
from .hierarchy import CHANNELS, PathSet, build_paths, region_total_paths
from .lost_demand import lost_demand
from .pipeline import EngineRun
from .uncertainty import EVENT_SCENARIO, SCENARIO, scenario_sigma

HORIZON_DAYS = {"7d": 7, "14d": 14, "28d": 28, "1m": 30, "2m": 60, "3m": 91, "8w": 56}


def _next_summer(day: pd.Timestamp) -> list[pd.Timestamp]:
    y = day.year if day.month <= 2 else day.year + 1
    return [pd.Timestamp(year=y, month=m, day=1) for m in (3, 4, 5, 6)]


def censored_share(run: EngineRun, flavor: str, window_days: int = 28) -> float:
    s = run.series.get((flavor, "ALL"))
    if s is None:
        return 0.0
    c = (s.cens >= 0.5)[-window_days:]
    return float(c.mean()) if len(c) else 0.0


def node_paths(run: EngineRun, flavor: str, channel: str, region: str | None, depot: str | None, days: int, n: int
               ) -> tuple[np.ndarray, PathSet]:
    key = ("paths", flavor, channel, region, depot, days, n)
    cache = run.__dict__.setdefault("_pcache", {})
    if key in cache:
        return cache[key]
    if depot:
        ps = build_paths(run, flavor, depot, days, n)
        arr = ps.total if channel in (None, "ALL") else ps.by_channel[channel]
    else:
        arr, ps = region_total_paths(run, flavor, region or "ALL", days, n, channel or "ALL")
    cache[key] = (arr, ps)
    return arr, ps


def season_paths(run: EngineRun, flavor: str, scope: str, n: int, channel: str = "ALL") -> tuple[np.ndarray, dict]:
    """SEASON horizon: sum of PATHS over the four summer months, never of P50s (spec M2 step 10)."""
    p = run.params
    months = _next_summer(run.day)
    regions = run.ds.regions if scope == "ALL" else ([scope] if scope in run.ds.regions else [run.ds.depot_region()[scope]])
    ec = rng_for(run.day, flavor, "season-common").standard_normal(n)
    tot = np.zeros(n)
    info = {"methods": set(), "rho": []}
    chans = CHANNELS if channel == "ALL" else (channel,)
    for m in months:
        h = (m + pd.Timedelta(days=m.days_in_month // 2) - run.day).days
        sig = scenario_sigma(h, "region", p)
        for r in regions:
            for ch in chans:
                node = run.monthly_node(flavor, r, ch, m)
                if node is None:
                    continue
                info["methods"].add(node["method"])
                info["rho"].append(node.get("rho", 1.0))
                en = rng_for(run.day, flavor, r, ch, m, "season-node").standard_normal(n)
                tot += node["p50"] * np.exp(sig * (np.sqrt(p["common_shock_rho"]) * ec + np.sqrt(1 - p["common_shock_rho"]) * en))
    if scope not in ("ALL",) and scope not in run.ds.regions:                      # depot: share of the region
        from .hierarchy import depot_shares, dirichlet_draw
        reg = run.ds.depot_region()[scope]
        sh = depot_shares(run, flavor, reg, "GTMT")
        draw = dirichlet_draw(sh, n, rng_for(run.day, flavor, reg, "season-dirichlet"))
        tot = tot * draw[:, sh.depots.index(scope)]
    info["methods"] = sorted(info["methods"])
    return tot, info


def _row(run: EngineRun, flavor: str, channel: str, region: str | None, depot: str | None, horizon: str, tot: np.ndarray,
         interval: str, grain: str, warnings: list[str]) -> dict:
    s = summarise(tot)
    lb = censored_share(run, flavor) > run.params["censored_fraction_limit"]
    w = list(warnings)
    if lb:
        w.append("LOWER_BOUND_ONLY")
    return {"asof": str(run.day.date()), "flavor": flavor, "channel": channel or "ALL", "region": region, "depot": depot,
            "horizon": horizon, "p10": s["p10"], "p50": s["p50"], "p90": s["p90"], "mean": s["mean"],
            "interval_type": interval, "grain_tag": grain, "provenance_tag": "SYNTHETIC+ESTIMATED",
            "extrapolating": flavor in run.launch_models, "censor_adjusted": True, "lower_bound_only": lb,
            "warnings": w, "model_version": MODEL_VERSION, "config_hash": run.params.config_hash,
            "data_freshness": str(run.ts)}


def get_forecast(run: EngineRun, flavor: str | None = None, channel: str | None = None, region: str | None = None,
                 depot: str | None = None, horizon: str = "7d", n: int | None = None) -> list[dict]:
    """out_forecast rows. horizon in 7d, 14d, 28d, 1m, 2m, 3m, 8w, SEASON."""
    n = n or run.params["n_paths"]
    out = []
    for fl in ([flavor] if flavor else FLAVORS):
        ch = channel or "ALL"
        if horizon == "SEASON":
            tot, info = season_paths(run, fl, depot or region or "ALL", n, ch)
            warn = ["EXTRAPOLATING"] if fl in run.launch_models else []
            out.append(_row(run, fl, ch, region, depot, horizon, tot, SCENARIO, "PHASED", warn))
            continue
        days = HORIZON_DAYS[horizon]
        arr, ps = node_paths(run, fl, ch, region, depot, days, n)
        tot = arr[:, :days].sum(axis=1)
        grain = "OBSERVED_GRAIN" if ch == "QC" else "PHASED"
        out.append(_row(run, fl, ch, region, depot, horizon, tot, ps.interval_type, grain, ps.warnings))
    return out


def get_daily_path(run: EngineRun, flavor: str, channel: str, region: str | None = None, depot: str | None = None,
                   days: int = 28, n: int | None = None) -> list[dict]:
    """Daily quantiles per calendar day (each day's quantile of the path samples; never summed across days)."""
    n = n or run.params["n_paths"]
    arr, ps = node_paths(run, flavor, channel, region, depot, days, n)
    rows = []
    grain = "OBSERVED_GRAIN" if channel == "QC" else "PHASED"
    for j, d in enumerate(ps.dates[:days]):
        col = arr[:, j]
        rows.append({"asof": str(run.day.date()), "flavor": flavor, "channel": channel, "region": region, "date": str(d.date()),
                     "p10": float(np.quantile(col, .1)), "p50": float(np.quantile(col, .5)), "p90": float(np.quantile(col, .9)),
                     "grain_tag": grain})
    return rows


def spike_rows(run: EngineRun, flavor: str | None = None, region: str | None = None, direction: str | None = None,
               include_all_scope: bool = True) -> list[dict]:
    """out_event rows for every (flavor, region) series plus the national series."""
    p = run.params
    rows = []
    scopes = ([region] if region else run.ds.regions + (["ALL"] if include_all_scope else []))
    for fl in ([flavor] if flavor else FLAVORS):
        for rg in scopes:
            det = run.detection(fl, rg)
            if det is None or det.df.empty:
                continue
            df = det.df
            last = df.iloc[-1]
            if direction and last["direction"] != direction and last["state"] in ("NORMAL",):
                continue
            base = run.qc_baseline_future(fl, rg, pd.date_range(run.day, periods=28))
            ei = EV.event_impact(det, p, base, rng_for(run.day, fl, rg, "spike"), n=2000)
            tiers = df["tier"] if "tier" in df else pd.Series(["NONE"] * len(df))
            first = {}
            for t in ("WATCH", "ALERT", "CRITICAL"):
                m = df[tiers == t]
                first[t] = None if m.empty else str(m.iloc[0]["available_on"].date())
            row = {"asof": str(run.day.date()), "flavor": fl, "region": rg, "direction": last["direction"] if last["state"] != "NORMAL" else None,
                   "signal": "UNITS_SOLD", "stage": last["state"], "cusum_S": float(max(last["S_up"], last["S_dn"])),
                   "S_up": float(last["S_up"]), "S_dn": float(last["S_dn"]), "ratio_R": float(last["R"]), "tier": str(tiers.iloc[-1]),
                   "last_data_day": str(pd.Timestamp(last["date"]).date()), "available_on": str(pd.Timestamp(last["available_on"]).date()),
                   "censored_today": bool(last["censored"]), "censored_share_28d": float((df["cens"].tail(28) >= 0.5).mean()),
                   "first_dates": first, "sigma_e": float(det.sigma_e), "launch_mode": bool(last["launch_mode"]),
                   "provenance": ["SYNTHETIC", "ESTIMATED"], "warnings": ["UNITS_SOLD_SIGNAL"] + (["EXTRAPOLATING"] if fl in run.launch_models else [])}
            if ei is not None:
                row.update({
                    "weights": {nm: float(w) for nm, w in zip(ei.scenario_names, ei.weights)},
                    "shape_unknown": ei.shape_unknown, "R_p": ei.R_p, "R_basis": ei.r_basis,
                    "extra_bottles": {str(h): v for h, v in ei.extra.items()},
                    "revenue_at_risk": {str(h): v for h, v in ei.revenue_at_risk.items()},
                    "margin_at_risk": {str(h): {k: x * p["margin"] / 1.0 for k, x in v.items()} for h, v in ei.revenue_at_risk.items()},
                    "interval_type": EVENT_SCENARIO, "event_direction": ei.direction})
                if ei.shape_unknown:
                    row["warnings"].append("EVENT_SHAPE_UNKNOWN")
            rows.append(row)
    return rows


def get_spike_status(run: EngineRun, flavor: str | None = None, region: str | None = None, direction: str | None = None) -> list[dict]:
    return spike_rows(run, flavor, region, direction)


def get_depot_depletion(run: EngineRun, flavor: str | None = None, depot: str | None = None) -> list[dict]:
    """M4b alerts by depot and flavor with cover days; pass-through estimate is reported separately by the service layer."""
    from .depletion import depletion_message
    names = run.ds.depot_name()
    fn = run.ds.flavor_name()
    rows = []
    for fl in ([flavor] if flavor else FLAVORS):
        for dp in ([depot] if depot else run.ds.depots):
            res = run.depletion(fl, dp)
            if res is None or res.table.empty:
                continue
            last = res.last
            if not np.isfinite(last.get("expected", np.nan)):
                continue
            rows.append({"asof": str(run.day.date()), "flavor": fl, "depot": dp, "week_start": str(pd.Timestamp(last["week_start"]).date()),
                         "closing": float(last["closing"]), "outflow": float(last["implied_outflow"]), "expected": float(last["expected"]),
                         "ratio": float(last["ratio"]), "S_up": float(last["S_up"]), "S_dn": float(last["S_dn"]), "state": last["state"],
                         "cover_days": None if not np.isfinite(last["cover_days"]) else float(last["cover_days"]),
                         "censored": bool(last["censored"]), "running_down_faster_than_plan": bool(last.get("running_down_faster_than_plan", False)),
                         "message": depletion_message(fl, dp, last, (fn[fl], names[dp])), "data_quality_gap": bool(last["balance_gap"]),
                         "provenance": ["SYNTHETIC", "CALCULATED"], "freshness_days": float((run.ts - pd.Timestamp(last["available_on"])).days)})
    return rows


def get_demand_for_inventory(run: EngineRun, flavor: str, depot: str, channel: str = "ALL", lead_time_days: int = 7,
                             review_days: int = 7, n: int | None = None, surge: float = 1.0, rho: float | None = None) -> dict:
    """Lead-time and protection-period totals plus the sample paths. Inventory must work from these, never from summed
    daily P90s: the P90 of a sum is below the sum of P90s whenever days are not perfectly correlated."""
    n = n or run.params["n_paths"]
    ps = build_paths(run, flavor, depot, int(lead_time_days + review_days), n, surge=surge, rho=rho)
    arr = ps.total if channel in (None, "ALL") else ps.by_channel[channel]
    lt = arr[:, :int(lead_time_days)].sum(axis=1)
    pt = arr.sum(axis=1)
    return {"lead_time_total": summarise(lt), "protection_total": summarise(pt), "paths": arr,
            "dates": ps.dates, "by_channel": ps.by_channel, "interval_type": ps.interval_type,
            "provenance": ["SYNTHETIC", "ESTIMATED"], "lower_bound_only": censored_share(run, flavor) > run.params["censored_fraction_limit"],
            "data_freshness": str(run.ts), "warnings": ps.warnings, "detail": ps.detail, "grain": ps.grain}


def get_lost_demand(run: EngineRun, flavor: str | None = None, since: pd.Timestamp | None = None) -> list[dict]:
    return lost_demand(run, flavor, since)
