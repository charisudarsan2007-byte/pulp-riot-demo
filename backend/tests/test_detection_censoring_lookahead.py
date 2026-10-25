"""Censoring, no look-ahead, launch baseline and detector behaviour on the supplied data."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from pulpriot.data.loader import FLAVORS, asof_ts
from pulpriot.engine import events as EV
from pulpriot.engine.censor import censor_prob_from_cover, d_adj, level_update_allowed
from pulpriot.engine.lost_demand import lost_demand
from pulpriot.engine.monthly import monthly_frame, seasonal_profile, summer_share_check
from pulpriot.engine.pipeline import EngineRun
from pulpriot.engine.qc_daily import QCSeries
from pulpriot.settings import load_params

P = load_params()


# ---- no look-ahead ---------------------------------------------------------------------------------------------------
def test_snapshot_never_contains_rows_that_arrive_later(ds):
    for day in ("2026-03-02", "2026-03-24", "2026-05-04", "2026-07-15"):
        ts = asof_ts(day)
        snap = ds.asof(ts)
        for short, col in ds.arrival.items():
            df = getattr(snap, short)
            if len(df):
                assert df[col].max() <= ts, (short, day)


def test_qc_available_next_morning_and_stock_report_after_receipt(ds):
    snap = ds.asof(asof_ts("2026-03-24"))
    assert snap.qc_daily["date"].max() == pd.Timestamp("2026-03-23")
    st = snap.stock_weekly
    assert st["report_received_at"].max() <= asof_ts("2026-03-24")
    assert st["stock_date"].max() == pd.Timestamp("2026-03-23")


def test_monthly_sales_arrive_a_week_after_month_end(ds):
    mf = monthly_frame(ds.asof(asof_ts("2026-03-24")), P)
    assert mf.last_month == pd.Timestamp("2026-02-01")                    # March is not yet in
    mf2 = monthly_frame(ds.asof(asof_ts("2026-04-09")), P)
    assert mf2.last_month == pd.Timestamp("2026-03-01")


def test_detector_state_at_a_day_does_not_depend_on_later_rows(svc):
    full = svc.run("2026-05-20").detection("DP", "ALL").df
    early = svc.run("2026-04-01").detection("DP", "ALL").df
    d = early["date"].max()
    a = early[early["date"] == d].iloc[0]
    b = full[full["date"] == d].iloc[0]
    assert a["state"] == b["state"] and a["direction"] == b["direction"]
    assert abs(a["S_up"] - b["S_up"]) < 1.0                                 # weekday multipliers use as-of data, so tiny drift only


def test_series_stop_at_the_last_arrived_day(svc):
    run = svc.run("2026-03-24")
    for s in run.series.values():
        assert s.dates.max() == pd.Timestamp("2026-03-23")


# ---- censoring ---------------------------------------------------------------------------------------------------------
def test_censor_probability_thresholds():
    assert censor_prob_from_cover(0.5, P) == 1.0
    assert censor_prob_from_cover(2.0, P) == 0.5
    assert censor_prob_from_cover(5.0, P) == 0.0


def test_censored_observation_below_p50_is_missing_above_updates():
    assert level_update_allowed(1.0, 80.0, 100.0) == "MISSING"
    assert level_update_allowed(1.0, 120.0, 100.0) == "UPDATE"
    assert level_update_allowed(0.0, 80.0, 100.0) == "UPDATE"


def test_d_adj_is_marked_and_never_below_observed():
    v, adjusted = d_adj(80.0, 1.0, 100.0)
    assert adjusted and v == 100.0
    v, adjusted = d_adj(80.0, 0.0, 100.0)
    assert not adjusted and v == 80.0


def _synthetic_series(n=70, level=1000.0, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n)
    q = level * np.exp(0.05 * rng.standard_normal(n))
    return dates, q


def test_stockout_days_cannot_lower_the_level_or_open_a_down_event():
    dates, q = _synthetic_series()
    cens = np.zeros(len(q))
    q2, c2 = q.copy(), cens.copy()
    q2[40:55] = q2[40:55] * 0.3                                               # sales collapse because shelves are empty
    c2[40:55] = 1.0
    sd = np.zeros(len(q))
    base = EV.run_detector(QCSeries("X", "ALL", dates, q, cens), sd, P)
    cap = EV.run_detector(QCSeries("X", "ALL", dates, q2, c2), sd, P)
    assert cap.df[cap.df.date.between(dates[40], dates[54])]["state"].isin(["NORMAL"]).all()
    assert (cap.df["S_dn"].max() < P["cusum_lower_h"])
    assert abs(cap.a_level - base.a_level) < 0.1                               # the level did not learn the collapse


def test_the_same_collapse_uncensored_does_open_a_down_event():
    dates, q = _synthetic_series()
    q2 = q.copy()
    q2[40:60] = q2[40:60] * 0.3
    det = EV.run_detector(QCSeries("X", "ALL", dates, q2, np.zeros(len(q))), np.zeros(len(q)), P)
    assert (det.df["direction"] == "DOWN").any()


def test_upper_event_detected_and_tiered_by_ratio():
    dates, q = _synthetic_series(n=110)
    q2 = q.copy()
    start = 76                                                  # 17 March: outside the month-boundary guard
    q2[start:] = q2[start:] * np.minimum(4 ** (np.arange(110 - start) / 14), 4)
    det = EV.run_detector(QCSeries("X", "ALL", dates, q2, np.zeros(110)), np.zeros(110), P)
    tiers = det.df[det.df.date >= dates[start]]["tier"].tolist()
    assert "ALERT" in tiers and "CRITICAL" in tiers
    first_alert = det.df[det.df.tier == "ALERT"].iloc[0]["date"]
    assert first_alert <= dates[start] + pd.Timedelta(days=12)


def test_calm_series_stays_quiet():
    dates, q = _synthetic_series(n=120, seed=4)
    det = EV.run_detector(QCSeries("X", "ALL", dates, q, np.zeros(120)), np.zeros(120), P)
    assert not det.df["state"].isin(["ONSET", "PEAK", "DECAY", "SUSTAINED"]).any()


def test_signal_tag_is_units_sold(run_spike):
    assert run_spike.series[("DP", "ALL")].signal == "UNITS_SOLD_SIGNAL"


# ---- launch ---------------------------------------------------------------------------------------------------------------
def test_launch_ramp_alone_raises_no_alert_before_the_reel(svc):
    det = svc.run("2026-03-15").detection("DP", "ALL")
    assert not det.df["tier"].isin(["ALERT", "CRITICAL"]).any()


def test_launch_ramp_plus_spike_raises_alert(svc):
    det = svc.run("2026-03-25").detection("DP", "ALL")
    assert det.df["tier"].isin(["ALERT", "CRITICAL"]).any()
    first = det.df[det.df["tier"].isin(["ALERT", "CRITICAL"])].iloc[0]
    assert pd.Timestamp("2026-03-18") <= first["available_on"] <= pd.Timestamp("2026-03-25")


def test_launch_uses_both_analogs_and_marks_extrapolation(run_spike):
    lm = run_spike.launch_models["DP"]
    assert {a["id"] for a in lm.analogs} == {"DB", "PP"}
    assert all(0 < a["similarity"] <= 1 for a in lm.analogs)
    assert any("outside" in n.lower() or "before the data window" in n.lower() for n in lm.notes)


def test_cold_start_shift_n0_returns_prior_and_sd_shrinks_with_n(run_spike):
    lm = run_spike.launch_models["DP"]
    s0, sd0 = lm.shift(np.array([]), np.array([]))
    assert s0 == 0.0
    obs, pri = np.full(1, 100.0), np.full(1, 140.0)
    s1, sd1 = lm.shift(obs, pri)
    s5, sd5 = lm.shift(np.full(5, 100.0), np.full(5, 140.0))
    assert sd0 > sd1 > sd5
    assert abs(s5 - np.log(100 / 140)) < abs(s1 - np.log(100 / 140))          # converges toward the observed ratio


def test_dragon_passion_baseline_is_not_the_withdrawn_constant(run_spike):
    s = run_spike.series[("DP", "ALL")]
    base = run_spike.qc_baseline_future("DP", "ALL", pd.date_range("2026-03-24", periods=7))
    assert not np.any(np.isclose(base, 18885, rtol=0.01)) and not np.any(np.isclose(base, 56656, rtol=0.01))


# ---- seasonality, lost demand ------------------------------------------------------------------------------------------------
def test_seasonal_profile_masks_censored_months(ds):
    snap = ds.asof(asof_ts("2026-08-20"))
    mf = monthly_frame(snap, P)
    g_masked, _ = seasonal_profile(mf, "PP", "QC", P)
    open_params = P.with_overrides(seasonal_mask_censored=False)
    g_open, _ = seasonal_profile(mf, "PP", "QC", open_params)
    assert not np.allclose(g_masked, g_open)                                   # censored months were excluded from the masked profile


def test_summer_share_consistency_check(svc):
    assert svc.trust("2026-06-30")["summer_share_check"]["status"] in ("OK", "INSUFFICIENT_HISTORY")  # a full Jul-Jun year has not arrived inside the Mar-Jun window
    assert svc.trust("2026-03-24")["summer_share_check"]["status"] == "INSUFFICIENT_HISTORY"


def test_lost_demand_only_on_censored_periods(svc):
    run = svc.run("2026-03-12")
    rows = [r for r in lost_demand(run, n=200) if r["channel"] == "ALL"]
    assert all(r["lost_p50"] == 0 for r in rows)                                # nothing is out of stock yet
    run = svc.run("2026-04-10")
    dp = next(r for r in lost_demand(run, "DP", n=300) if r["channel"] == "ALL")
    assert dp["lost_p10"] <= dp["lost_p50"] <= dp["lost_p90"] and dp["lost_p50"] > 0
    mpb = next(r for r in lost_demand(run, "MPB", n=200) if r["channel"] == "ALL")
    assert mpb["lost_p50"] == 0


def test_conformal_source_never_uses_company_archive():
    import inspect
    from pulpriot.engine import uncertainty
    src = inspect.getsource(uncertainty)
    assert "forecast_history" not in src and "fact_forecast_archive" not in src.replace("never the company", "")
    assert P["conformal_source"] == "engine_backtest"
