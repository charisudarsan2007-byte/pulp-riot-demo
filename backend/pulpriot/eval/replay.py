"""Crisis replay (H1-H3): run the engine for each as-of date and keep a compact row, with data availability enforced.

Every row is built from an `EngineRun` for that date, which can only see rows that had arrived by then. Hidden synthetic truth
(true latent demand) is attached ONLY by `with_truth`, which is used by the explicit evaluation view; it is never an input to
the engine and is imported lazily here so the dependency boundary is visible."""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from ..engine.forecast import spike_rows
from ..engine.pipeline import EngineRun
from ..supply import pulp as PU

LAUNCH = pd.Timestamp("2026-03-02")


def replay_row(run: EngineRun, flavor: str = "DP") -> dict:
    """Compact state for one as-of date."""
    day = run.day
    row: dict = {"asof": str(day.date())}
    sp = spike_rows(run, flavor, region="ALL")
    s = sp[0] if sp else None
    det = run.detection(flavor, "ALL")
    if s is not None and det is not None and not det.df.empty:
        df = det.df
        last = df.iloc[-1]
        unc = df[df["cens"] < 0.5]
        row.update({"stage": s["stage"], "tier": s["tier"], "ratio_today": s["ratio_R"], "S_up": s["S_up"], "S_dn": s["S_dn"],
                    "ratio_last_uncensored": float(unc.iloc[-1]["R"]) if len(unc) else None, "ratio_peak_uncensored": float(unc["R"].max()) if len(unc) else None,
                    "q_last": float(last["q"]), "baseline_last": float(last["baseline_units"]), "censored_today": bool(last["censored"]),
                    "first_watch": s["first_dates"].get("WATCH"), "first_alert": s["first_dates"].get("ALERT"), "first_critical": s["first_dates"].get("CRITICAL"),
                    "last_data_day": s["last_data_day"], "extra_28d_p50": (s.get("extra_bottles") or {}).get("28", {}).get("p50"),
                    "revenue_at_risk_28d_p50": (s.get("revenue_at_risk") or {}).get("28", {}).get("p50"), "shape_unknown": s.get("shape_unknown")})
    q = run.snap.qc_daily
    q = q[(q.flavor_id == flavor) & (q.date == day - pd.Timedelta(days=1))]
    row["depots_flagged_out_yesterday"] = int((q["stockout_flag"] == 1).sum()) if len(q) else 0
    pf = next((x for x in PU.pulp_status(run, 150) if x["ingredient"] == "ING_PF"), None)
    if pf:
        ex = PU.closure_exposure(run, 21)
        row.update({"pf_runway_days": pf["runway_days"], "route_status": (pf["route"] or {}).get("status"), "pf_tier_if_21d_closure": ex["tier"],
                    "pf_kg_short_if_21d": ex["kg_short"]})
    return row


def replay_table(factory: Callable[[pd.Timestamp], EngineRun], dates: list[pd.Timestamp], flavor: str = "DP") -> list[dict]:
    return [replay_row(factory(d), flavor) for d in dates]


def with_truth(rows: list[dict], flavor: str = "DP") -> list[dict]:
    """Evaluation view only: attach hidden synthetic latent demand and the injected multiplier label for the same days."""
    from ..data.truth import Truth                                   # truth boundary: imported here, never in the engine
    tr = Truth()
    dem = tr.demand_daily()
    dem = dem[dem.flavor_id == flavor].groupby("date").agg(demand_true=("demand_true", "sum"), sold=("sold", "sum"), lost=("lost", "sum"))
    lab = tr.labels_daily()
    lab = lab[lab.flavor_id == flavor].groupby("date").agg(injected_multiplier=("injected_qc_multiplier", "mean"))
    out = []
    for r in rows:
        d = pd.Timestamp(r["asof"]) - pd.Timedelta(days=1)
        x = dict(r)
        if d in dem.index:
            x["truth_latent_demand_all_channels"] = float(dem.loc[d, "demand_true"])
            x["truth_lost_all_channels"] = float(dem.loc[d, "lost"])
        if d in lab.index:
            x["truth_injected_multiplier"] = float(lab.loc[d, "injected_multiplier"])
        out.append(x)
    return out


def qc_series_view(run: EngineRun, flavor: str = "DP", region: str = "ALL") -> dict:
    """Observed QC daily sales and the detector's baseline and ratio up to the as-of date."""
    det = run.detection(flavor, region)
    if det is None or det.df.empty:
        return {"rows": []}
    df = det.df
    rows = [{"date": str(r.date.date()), "available_on": str(r.available_on.date()), "q": float(r.q), "baseline": float(r.baseline_units), "R": float(r.R),
             "state": r.state, "tier": getattr(r, "tier", "NONE"), "S_up": float(r.S_up), "S_dn": float(r.S_dn), "censored": bool(r.censored),
             "cens_share": float(r.cens)} for r in df.itertuples()]
    return {"rows": rows, "signal": "UNITS_SOLD_SIGNAL", "flavor": flavor, "region": region}
