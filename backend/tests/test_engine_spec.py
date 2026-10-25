"""Spec v1.1 section 9 tests with expected numbers, plus the red lines."""
import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from pulpriot.engine import events as EV
from pulpriot.engine.combine import combine_log, combine_totals, nested_rescale
from pulpriot.engine.common import lognormal_mean, rng_for, summarise
from pulpriot.engine.hierarchy import build_paths, depot_shares, dirichlet_draw, region_total_paths
from pulpriot.engine.launch import template_at
from pulpriot.engine.uncertainty import coverage_of_pct_band, scenario_sigma
from pulpriot.engine.forecast import get_demand_for_inventory, get_forecast
from pulpriot.settings import load_params

P = load_params()


# ---- CUSUM numbers --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sigma,day", [(0.10, 4), (0.25, 7)])
def test_upper_cusum_noise_free_ramp(sigma, day):
    z = [np.log(4 ** (j / 14)) / sigma for j in range(1, 40)]
    assert EV.first_crossing(np.array(z), P["cusum_k"], P["cusum_h"], True) == day


@pytest.mark.parametrize("sigma,day", [(0.10, 4), (0.25, 17)])
def test_lower_cusum_noise_free_step(sigma, day):
    z = np.full(40, np.log(0.8) / sigma)
    assert EV.first_crossing(z, P["cusum_k"], P["cusum_h"], up=False) == day


def test_ratio_rule_on_the_same_ramp():
    R = np.array([4 ** (j / 14) for j in range(1, 30)])
    assert int(np.argmax(R >= 2)) + 1 == 7
    assert int(np.argmax(R >= 3)) + 1 == 12


def test_false_alarm_rate_at_h65_is_far_below_h5():
    rng = np.random.default_rng(26)
    def arl(h, runs=150, horizon=6000):
        out = []
        for _ in range(runs):
            S = 0.0
            for i, z in enumerate(rng.standard_normal(horizon), 1):
                S = max(0.0, S + z - 0.5)
                if S >= h:
                    break
            out.append(i)
        return np.mean(out)
    assert arl(6.5) > 2.5 * arl(5.0)


def test_error_scale_and_coverage_of_30pct_band():
    assert abs(0.30 / np.sqrt(2 / np.pi) - 0.376) < 0.001
    assert abs(coverage_of_pct_band() - 0.575) < 0.01


def test_scenario_weight_floor_and_sum():
    w = EV.floor_weights(np.array([.001, .001, .001, .001, .996]), P["scen_floor"])
    assert w.min() >= 0.05 and abs(w.sum() - 1.0) < 1e-12
    for raw in np.random.default_rng(1).dirichlet(np.ones(5) * 0.2, size=50):
        ww = EV.floor_weights(raw, P["scen_floor"])
        assert ww.min() >= 0.05 - 1e-12 and abs(ww.sum() - 1) < 1e-12


def test_weights_are_prior_until_four_post_peak_days(run_spike):
    det = run_spike.detection("DP", "ALL")
    ei = EV.event_impact(det, P, np.full(40, 20000.0), rng_for("t"), n=500)
    assert ei is not None and ei.shape_unknown
    assert np.allclose(ei.weights, EV.floor_weights(EV.prior_weights(P), P["scen_floor"]))
    assert ei.weights.min() >= 0.05 and abs(ei.weights.sum() - 1) < 1e-12


def test_scenario_weights_after_peak_never_below_floor():
    R_p, R_max = 3.0, 4.5
    j = np.arange(1, 9)
    curves = EV.scenario_curves(R_p, R_max, j)
    obs = np.log(curves[3])                                      # data that looks exactly like the plateau
    w = EV.scenario_weights(obs, curves, 0.08, EV.prior_weights(P), P["scen_temper"], P["scen_floor"])
    assert w.argmax() == 3 and w.min() >= 0.05 and abs(w.sum() - 1) < 1e-12


def test_mean_with_kappa():
    assert abs(lognormal_mean(10, 0.5, 1) - 11.465) < 0.001
    assert abs(lognormal_mean(10, 0.5, 1) - 11.331) > 0.1


def test_temporal_combine_limits():
    assert abs(combine_log(np.log(100), 0.01, np.log(500), 1e12) - np.log(100)) < 1e-6          # v_M huge -> daily total
    assert abs(np.exp(combine_log(np.log(100), 0.04, np.log(400), 0.04)) - 200) < 1e-6          # equal variances -> geometric mean


def test_nested_rescale_sums_exactly():
    rng = np.random.default_rng(3)
    paths = rng.lognormal(3, 0.3, size=(500, 35))
    monthly = np.full(35, 25.0)
    T, _ = combine_totals(paths, monthly, "region", False, P)
    out, _ = nested_rescale(paths, T)
    med = np.median(out[:, :28], axis=0)
    assert abs(med[:7].sum() - T[7]) < 1e-6 and abs(med[:14].sum() - T[14]) < 1e-6 and abs(med[:28].sum() - T[28]) < 1e-6
    assert T[14] >= T[7] and T[28] >= T[14]


def test_p90_of_summed_paths_below_sum_of_p90s():
    rng = np.random.default_rng(5)
    common = rng.standard_normal(4000)
    rho = 0.5
    nodes = [np.exp(0.4 * (np.sqrt(rho) * common + np.sqrt(1 - rho) * rng.standard_normal(4000))) for _ in range(4)]
    assert np.quantile(sum(nodes), 0.9) < sum(np.quantile(n, 0.9) for n in nodes)


def test_template_has_no_steps_when_interpolated():
    tmpl = np.array([.35, .6, .8, .95, 1, 1, .98, .97])
    d = np.arange(0, 56)
    smooth = template_at(d, tmpl, True)
    assert np.max(np.abs(np.diff(smooth))) < 0.06
    step = template_at(d, tmpl, False)
    assert step[6] == tmpl[0] and step[7] == tmpl[1]


# ---- coherence and the inventory interface ---------------------------------------------------------------------------
def test_depot_paths_sum_to_region_exactly(run_spike):
    reg = run_spike.ds.depot_region()
    depots = [d for d, r in reg.items() if r == "R-N"]
    tot = sum(build_paths(run_spike, "MPB", d, days=10, n=200).total for d in depots)
    region, _ = region_total_paths(run_spike, "MPB", "R-N", 10, 200)
    assert np.abs(tot - region).max() < 1e-6


def test_dirichlet_draws_sum_to_one_and_means_are_coherent(run_spike):
    sh = depot_shares(run_spike, "MPB", "R-N", "GTMT")
    draw = dirichlet_draw(sh, 4000, rng_for("x"))
    assert np.allclose(draw.sum(axis=1), 1.0)
    assert np.allclose(draw.mean(axis=0), sh.pi_hat, atol=0.02)
    assert abs(sh.pi_hat.sum() - 1) < 1e-9


def test_depot_shares_not_hardcoded(run_spike, run_calm):
    a = depot_shares(run_spike, "GC", "R-S", "QC").pi_hat
    b = depot_shares(run_calm, "GC", "R-S", "QC").pi_hat
    assert not np.allclose(a, b)


def test_inventory_interface_returns_totals_and_paths_not_daily_quantiles(run_spike):
    d = get_demand_for_inventory(run_spike, "MPB", "DEP01", "ALL", 3, 7, n=500)
    assert set(d["lead_time_total"]) == {"p10", "p50", "p90", "mean"}
    assert d["paths"].shape == (500, 10)
    daily_p90_sum = sum(np.quantile(d["paths"][:, j], 0.9) for j in range(10))
    assert d["protection_total"]["p90"] < daily_p90_sum
    assert d["lead_time_total"]["p90"] < sum(np.quantile(d["paths"][:, j], 0.9) for j in range(3))
    assert d["provenance"] and d["interval_type"] and "data_freshness" in d


def test_forecast_rows_always_tagged(run_spike):
    for h in ("7d", "28d", "SEASON"):
        for r in get_forecast(run_spike, None, None, None, None, h, n=200):
            assert r["provenance_tag"] and r["interval_type"] and r["grain_tag"] and r["model_version"]
            assert r["p10"] <= r["p50"] <= r["p90"]
    ch = {r["grain_tag"] for r in get_forecast(run_spike, "MPB", "QC", None, None, "7d", n=200)}
    assert ch == {"OBSERVED_GRAIN"}
    ch = {r["grain_tag"] for r in get_forecast(run_spike, "MPB", "GT", None, None, "7d", n=200)}
    assert ch == {"PHASED"}


def test_forecast_is_not_capped_by_stock_or_capacity(run_spike):
    f = get_forecast(run_spike, "DP", "ALL", None, "DEP01", "28d", n=300)[0]
    sc = get_forecast(run_spike, "DP", "ALL", None, None, "28d", n=300)[0]
    assert f["p50"] > 0 and sc["p50"] > 0
    assert sc["p50"] > run_spike.params["price"]                         # sanity: bottles, not rupees


def test_event_interval_type_when_event_open(run_spike):
    assert get_forecast(run_spike, "DP", "ALL", None, None, "7d", n=200)[0]["interval_type"] == "EVENT_SCENARIO"
    assert get_forecast(run_spike, "MPB", "ALL", None, None, "7d", n=200)[0]["interval_type"] == "SCENARIO"


def test_gt_mt_are_phased_and_flat_inside_a_month(run_spike):
    ps = build_paths(run_spike, "MPB", "DEP01", days=10, n=300, rho=0.0)
    gt = np.median(ps.by_channel["GT"], axis=0)
    assert np.std(gt) / np.mean(gt) < 0.1                                # monthly total spread over days, no daily signal


def test_no_quantile_addition_in_season_horizon(run_spike):
    nat = get_forecast(run_spike, "MPB", "ALL", None, None, "SEASON", n=400)[0]
    parts = [get_forecast(run_spike, "MPB", "ALL", r, None, "SEASON", n=400)[0] for r in run_spike.ds.regions]
    assert nat["p90"] < sum(p["p90"] for p in parts) * 0.999


def test_runs_are_deterministic(svc):
    a = get_forecast(svc.run("2026-04-14"), "GC", "ALL", None, None, "28d", n=300)[0]
    b = get_forecast(svc.run("2026-04-14"), "GC", "ALL", None, None, "28d", n=300)[0]
    assert a["p50"] == b["p50"] and a["p10"] == b["p10"]
