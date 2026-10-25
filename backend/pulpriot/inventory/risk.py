"""Risk grid (A4), regional demand shift (C15), surge table (C17).

Status colours always come with a label: RED "Stockout likely", AMBER "Watch", GREEN "Covered", and an EXCESS flag for
cover above the overstock line. Cover comes from the same path-based stockout distribution used everywhere else."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.loader import FLAVORS
from ..engine.pipeline import EngineRun
from .position import recent_runrate_cover, stock_position, stockout_distribution


def classify(days_p10: float, days_p50: float, simple_cover: float | None, p) -> tuple[str, str, bool]:
    red, amber, over = p["risk_red_cover_days"], p["risk_amber_cover_days"], p["overstock_cover_days"]
    excess = bool(simple_cover is not None and simple_cover > over)
    if days_p10 < red:
        return "RED", "Stockout likely within a week", excess
    if days_p10 < amber or days_p50 < amber + 7:
        return "AMBER", "Cover is tightening", excess
    return "GREEN", "Covered", excess


def risk_grid(run: EngineRun, n: int = 200, horizon: int = 60) -> list[dict]:
    cache = run.__dict__.setdefault("_pcache", {})
    if ("grid",) not in cache:
        cache[("grid",)] = _risk_grid(run, n, horizon)
    return cache[("grid",)]


def _risk_grid(run: EngineRun, n: int, horizon: int) -> list[dict]:
    cells = []
    for fl in FLAVORS:
        for dp in run.ds.depots:
            pos = stock_position(run, fl, dp, n)
            so = stockout_distribution(run, fl, dp, horizon, n, pos=pos)
            rr = recent_runrate_cover(run, fl, dp, pos)
            status, label, excess = classify(so["days_p10"], so["days_p50"], so["simple_cover_days"], run.params)
            rc = rr.get("days")
            if rc is not None and rc < run.params["risk_red_cover_days"] and status != "RED":
                status, label = "RED", "Run-rate cover under a week"
            cells.append({"flavor": fl, "depot": dp, "status": status, "label": label, "excess": excess,
                          "stock_p50": so["stock_today_p50"], "days_p10": so["days_p10"], "days_p50": so["days_p50"],
                          "date_p10": so["date_p10"], "date_p50": so["date_p50"], "simple_cover_days": so["simple_cover_days"],
                          "runrate_cover_days": rc, "report_date": None if pos.report_date is None else str(pos.report_date.date()),
                          "report_age_days": pos.gap_days, "interval_type": so["interval_type"], "beyond_horizon": so["beyond_horizon_p50"]})
    return cells


def demand_shift(run: EngineRun) -> list[dict]:
    """Depots where actual demand (censor-adjusted, last four reported weeks) diverges from this month's supply plan (T12).

    shift = demand share of the flavor's national demand - allocation share. Flagged at 3 points and 20% relative."""
    wk = run.weekly
    al = run.snap.allocation_history
    if wk.empty or al.empty:
        return []
    m0 = pd.Timestamp(year=run.day.year, month=run.day.month, day=1)
    a = al[al.month == m0]
    if a.empty:
        a = al[al.month == al.month.max()]
    last4 = wk.sort_values("week_start").groupby(["depot_id", "flavor_id"]).tail(4).copy()
    rows = []
    for fl in FLAVORS:
        sub = last4[last4.flavor_id == fl]
        if sub.empty:
            continue
        dem = {}
        for dp, g in sub.groupby("depot_id"):
            exp = run.expected_weekly(g.sort_values("week_start"), fl, dp)
            obs = g.sort_values("week_start")["implied_outflow"].clip(lower=0).to_numpy()
            cens = g.sort_values("week_start")["censored"].to_numpy()
            adj = np.where(cens, np.maximum(obs, np.nan_to_num(exp, nan=0.0)), obs)        # CENSOR_ADJUSTED, shares only
            dem[dp] = float(adj.sum())
        tot_d = sum(dem.values())
        al_f = a[a.flavor_id == fl].set_index("depot_id")["allocated_bottles"]
        tot_a = float(al_f.sum())
        if tot_d <= 0 or tot_a <= 0:
            continue
        for dp in run.ds.depots:
            ds_ = dem.get(dp, 0.0) / tot_d
            as_ = float(al_f.get(dp, 0.0)) / tot_a
            diff = ds_ - as_
            rel = diff / as_ if as_ > 0 else 0.0
            flag = "UNDER_SUPPLIED" if (diff >= 0.03 and rel >= 0.2) else "OVER_SUPPLIED" if (diff <= -0.03 and rel <= -0.2) else "IN_LINE"
            rows.append({"flavor": fl, "depot": dp, "demand_share": ds_, "allocation_share": as_, "shift_pp": diff * 100, "relative": rel,
                         "flag": flag, "demand_basis": "last 4 reported weeks, censor-adjusted (shares only)",
                         "allocation_month": str(m0.date()), "provenance": ["SYNTHETIC", "CENSOR_ADJUSTED", "CALCULATED"]})
    return rows


def surge_table(run: EngineRun, flavor: str, multiplier: float, n: int = 300) -> list[dict]:
    """C17: how fast each depot runs out if demand runs at `multiplier` x from today (all channels)."""
    out = []
    for dp in run.ds.depots:
        pos = stock_position(run, flavor, dp, n)
        base = stockout_distribution(run, flavor, dp, 60, n, pos=pos)
        sg = stockout_distribution(run, flavor, dp, 60, n, pos=pos, surge=multiplier)
        out.append({"depot": dp, "stock_p50": base["stock_today_p50"], "base": {k: base[k] for k in ("days_p10", "days_p50", "date_p10", "date_p50")},
                    "surge": {k: sg[k] for k in ("days_p10", "days_p50", "date_p10", "date_p50")}, "multiplier": multiplier})
    return out
