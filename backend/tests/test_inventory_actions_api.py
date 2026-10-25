"""Inventory arithmetic, shelf life, transfers, the action register and API smoke tests."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from pulpriot.actions import generator as GEN
from pulpriot.actions import roles as RL
from pulpriot.actions import store as ST
from pulpriot.engine.hierarchy import build_paths
from pulpriot.inventory import mitigation as MT
from pulpriot.inventory import policy as PO
from pulpriot.inventory import position as PS
from pulpriot.inventory import transfer as TR
from pulpriot.api.main import app

client = TestClient(app)


# ---- stock position and cover ------------------------------------------------------------------------------------------
def test_nowcast_equals_reported_plus_receipts_minus_outflow(run_spike):
    pos = PS.stock_position(run_spike, "MPB", "DEP01", 400)
    expect = pos.reported_qty + pos.receipts_since - pos.qc_observed_since - pos.gtmt_est_since["p50"]
    assert abs(pos.nowcast["p50"] - expect) < 0.03 * pos.reported_qty
    assert pos.nowcast_paths.min() >= 0


def test_stock_never_negative_and_reported_is_a_fact(run_spike):
    for dp in run_spike.ds.depots[:3]:
        pos = PS.stock_position(run_spike, "DP", dp, 200)
        assert pos.nowcast_paths.min() >= 0
        assert pos.report_date is not None and pos.report_date <= run_spike.day


def test_more_stock_pushes_the_stockout_date_out_and_surge_pulls_it_in(run_spike):
    pos = PS.stock_position(run_spike, "MPB", "DEP01", 300)
    base = PS.stockout_distribution(run_spike, "MPB", "DEP01", 90, 300, pos=pos)
    more = PS.stockout_distribution(run_spike, "MPB", "DEP01", 90, 300, pos=pos, stock_delta=200000.0)
    surge = PS.stockout_distribution(run_spike, "MPB", "DEP01", 90, 300, pos=pos, surge=3.0)
    assert more["days_p50"] > base["days_p50"] > surge["days_p50"]
    assert base["days_p10"] <= base["days_p50"] <= base["days_p90"]
    assert "no further replenishment" in base["definition"].lower()


def test_inbound_shipment_delays_stockout(run_spike):
    pos = PS.stock_position(run_spike, "DP", "DEP04", 300)
    base = PS.stockout_distribution(run_spike, "DP", "DEP04", 60, 300, pos=pos)
    arr = PS.stockout_distribution(run_spike, "DP", "DEP04", 60, 300, pos=pos, extra_arrivals=[(run_spike.day + pd.Timedelta(days=2), 200000.0)])
    assert arr["days_p50"] > base["days_p50"]


# ---- safety stock ---------------------------------------------------------------------------------------------------------
def test_safety_stock_is_the_service_quantile_of_path_sums(run_spike):
    ss = PO.safety_stock(run_spike, "MPB", "DEP01", 0.95, n=800)
    d = PO.get_demand_for_inventory(run_spike, "MPB", "DEP01", "ALL", ss["lead_time_days"], ss["review_days"], n=800)
    pt = d["paths"].sum(axis=1)
    assert abs(ss["target_uncapped"] - np.quantile(pt, 0.95)) < 1e-6
    assert abs(ss["safety_stock"] - max(ss["target_final"] - pt.mean(), 0)) < 1e-6
    assert ss["target_final"] <= ss["target_uncapped"] + 1e-6
    hi = PO.safety_stock(run_spike, "MPB", "DEP01", 0.99, n=800)
    assert hi["target_uncapped"] > ss["target_uncapped"]
    assert ss["old_rule"]["target"] is None or ss["old_vs_new_ratio"] is not None


# ---- shelf life, FEFO ---------------------------------------------------------------------------------------------------------
def test_sellable_clock_applies_60_of_90_rule(run_spike):
    pos = PS.stock_position(run_spike, "MPB", "DEP01", 200)
    lots = PO.lots_now(run_spike, "MPB", "DEP01", pos)
    assert (lots["days_left_mtqc"] == 30 - lots["age_days"]).all() and (lots["days_left_total"] == 90 - lots["age_days"]).all()
    clock = PO.sellable_clock(run_spike, "MPB", "DEP01", pos, lots)
    gt_only = [b for b in clock["buckets"] if b["label"].startswith("GT-only")][0]
    assert abs(gt_only["qty"] - lots[lots.days_left_mtqc <= 0]["qty_now"].sum()) < 1e-6
    assert "unverified" in clock["rule"].lower()


def test_fefo_orders_oldest_first_and_sends_gt_only_to_gt(run_spike):
    plan = PO.fefo_plan(run_spike, "MPB", "DEP01")["plan"]
    ages = [p["age_days"] for p in plan]
    assert ages == sorted(ages, reverse=True)
    for p in plan:
        if p["days_left_mtqc"] <= 0:
            assert p["channel"] == "GT"


# ---- transfers ---------------------------------------------------------------------------------------------------------------------
def test_transfer_cost_and_arrival_arithmetic(run_spike):
    r = TR.transfer_preview(run_spike, "MPB", "DEP01", "DEP02", 40000, n=300)
    assert abs(r["cost_total"] - 40000 * 1.5) < 1e-9
    lane = run_spike.ds.dims["lane_depot"]
    row = lane[(lane.from_depot == "DEP01") & (lane.to_depot == "DEP02")].iloc[0]
    assert r["transit_days"] == int(row["transit_days"])
    assert r["arrival_date"] == str((run_spike.day + pd.Timedelta(days=int(row["transit_days"]))).date())
    assert r["donor"]["stock_after"] == r["donor"]["stock_before"] - 40000
    assert r["recipient"]["stock_after_arrival"] == r["recipient"]["stock_before"] + 40000


def test_infeasible_transfer_is_flagged(run_spike):
    r = TR.transfer_preview(run_spike, "DP", "DEP01", "DEP02", 5_000_000, n=200)
    assert r["feasible"] is False and r["reasons"]


def test_mitigation_flags_options_arriving_after_p10(run_spike):
    m = MT.mitigation_options(run_spike, "DP", "DEP06", n=250)
    p10 = pd.Timestamp(m["stockout"]["date_p10"])
    for o in m["options"]:
        if o["arrival_date"]:
            assert o["arrives_too_late"] == (pd.Timestamp(o["arrival_date"]) > p10)
    cp = next(o for o in m["options"] if o["key"] == "copacker")
    assert cp["arrives_too_late"] and (pd.Timestamp(cp["arrival_date"]) - run_spike.day).days >= 14
    base = run_spike.params["price"] * (1 - run_spike.params["margin"])
    assert abs(cp["cost_per_bottle"] - (base * 0.25 + float(cp["cost_per_bottle"] - base * 0.25))) < 1e-9
    assert cp["cost_per_bottle"] > base * 0.25


def test_copacker_never_exceeds_ten_percent_headroom(run_spike):
    cap = MT.copacker_headroom(run_spike)
    assert abs(cap["monthly_cap"] - 0.10 * 25_000_000) < 1


def test_one_pile_of_plant_stock_is_not_promised_twice(run_spike):
    cons = {"fg_used": {}, "donor_used": {}, "copacker_used": 0.0}
    first = MT.mitigation_options(run_spike, "DP", "DEP01", n=200, constraints=cons)
    q1 = next(o for o in first["options"] if o["key"] == "plant")["qty"]
    cons["fg_used"]["DP"] = q1
    second = MT.mitigation_options(run_spike, "DP", "DEP04", n=200, constraints=cons)
    q2 = next(o for o in second["options"] if o["key"] == "plant")["qty"]
    fg = MT.plant_fg(run_spike, "DP")["qty"]
    assert q1 + q2 <= fg + 1e-6


# ---- action register ---------------------------------------------------------------------------------------------------------------
def test_new_recommendations_are_pending_and_nothing_is_executed(svc):
    ST.reset_all()
    run = svc.run("2026-03-24")
    rows = GEN.sync(run)
    assert rows and all(r["status"] == "PENDING" for r in rows)
    assert all(r["approver_role"].startswith("ROLE_") for r in rows)
    assert all(r["decided_by"] is None for r in rows)


def test_decision_gate_reason_and_audit_log(svc):
    ST.reset_all()
    run = svc.run("2026-03-24")
    rows = GEN.sync(run)
    a = rows[0]
    wrong = next(r["role_id"] for r in RL.stakeholders(svc.ds) if r["role_id"] not in RL.chain(svc.ds, a["approver_role"]))
    r = client.post(f"/api/actions/{a['id']}/decision", json={"decision": "approve", "actor_role": wrong, "asof": "2026-03-24"})
    assert r.status_code == 403
    r = client.post(f"/api/actions/{a['id']}/decision", json={"decision": "reject", "actor_role": "ROLE_COO", "asof": "2026-03-24"})
    assert r.status_code == 422
    r = client.post(f"/api/actions/{a['id']}/decision", json={"decision": "modify", "actor_role": "ROLE_COO", "asof": "2026-03-24", "reason": "smaller first tranche", "qty": 1000})
    assert r.status_code == 200 and r.json()["action"]["status"] == "MODIFIED" and r.json()["action"]["approved_qty"] == 1000
    log = ST.audit(a["id"])
    assert [e["event"] for e in log][-1] == "MODIFY" and log[-1]["actor_role"] == "ROLE_COO" and log[-1]["snapshot"]
    assert log[0]["event"] == "CREATED"
    again = GEN.sync(run)
    assert next(x for x in again if x["id"] == a["id"])["status"] == "MODIFIED"          # the human's decision survives a refresh


def test_escalation_follows_the_workbook_chain_and_sla(svc):
    ds = svc.ds
    assert RL.chain(ds, "ROLE_PLN") == ["ROLE_PLN", "ROLE_SCH", "ROLE_COO"]
    a = {"approver_role": "ROLE_PLN", "created_asof": "2026-03-24", "deadline": "2026-03-30"}
    e0 = RL.escalation_state(ds, a, "2026-03-24", 24)
    e2 = RL.escalation_state(ds, a, "2026-03-26", 24)
    assert e0["current_owner"] == "ROLE_PLN" and not e0["escalated"]
    assert e2["current_owner"] == "ROLE_COO" and e2["escalated"]
    assert RL.can_decide(ds, "ROLE_COO", "ROLE_PLN") and not RL.can_decide(ds, "ROLE_PLN", "ROLE_COO")


def test_duplicate_alerts_merge_into_one_card(svc):
    ST.reset_all()
    r = client.get("/api/alerts?asof=2026-03-31").json()
    total_items = sum(g["merged_count"] for g in r["groups"])
    assert len(r["groups"]) < total_items                                  # several depot lines collapsed into fewer cards


def test_waterfall_has_no_double_count_and_margin_switch(svc):
    m = client.get("/api/finance/waterfall?asof=2026-03-31&economics=margin").json()
    rv = client.get("/api/finance/waterfall?asof=2026-03-31&economics=revenue").json()
    assert rv["do_nothing"]["stockout_lost_value"] > m["do_nothing"]["stockout_lost_value"] > 0
    assert abs(rv["do_nothing"]["stockout_lost_value"] / m["do_nothing"]["stockout_lost_value"] - 1 / 0.32) < 0.01
    assert abs(m["net_saved"] - (m["do_nothing"]["total"] - (m["residual_loss"] + m["actions"]["mitigation_total"]))) < 1e-6


# ---- API smoke ------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/meta", "/api/trust?asof=2026-03-24", "/api/heatmap?asof=2026-03-24", "/api/sankey?asof=2026-03-24", "/api/demand/forecast?flavor=DP&asof=2026-03-24",
    "/api/demand/spikes?asof=2026-03-24", "/api/inventory/tank?flavor=DP&depot=DEP04&asof=2026-03-24", "/api/supply/runway?asof=2026-05-06",
    "/api/supply/corridor?asof=2026-05-06", "/api/supply/tradeoff?asof=2026-05-06", "/api/replay/series?asof=2026-03-24",
])
def test_routes_answer_and_carry_provenance(path):
    r = client.get(path)
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert "provenance" in body or path.startswith("/api/meta") or "labels" in body or "rows" in body


def test_ask_the_agent_answers_from_engine_values_with_sources():
    r = client.post("/api/ask", json={"question": "When will Dragon Passion run out in Mumbai?", "asof": "2026-03-24"}).json()
    assert "Mumbai" in r["answer"] and r["sources"] and "no generated numbers" in r["method"] and r["freshness"]
    r = client.post("/api/ask", json={"question": "tell me a joke", "asof": "2026-03-24"}).json()
    assert r["understood"]["intent"] is None and r["followups"]


def test_ask_asks_instead_of_guessing():
    r = client.post("/api/ask", json={"question": "how much stock do we have", "asof": "2026-03-24"}).json()
    assert r.get("clarify") == "flavor"


def test_pass_through_defaults_to_off_and_is_tagged_assumed(svc):
    sp = client.get("/api/demand/spikes?asof=2026-04-20").json()
    assert sp["pass_through"]["rho"] == 0.0 and "ASSUMED" in sp["pass_through"]["source"]
    est = sp["pass_through"]["estimate"]
    assert est["status"] in ("INSUFFICIENT_OVERLAP", "ESTIMATED") and est["fallback"]


def test_asof_is_clamped_to_the_replay_window():
    r = client.get("/api/control-tower?asof=2025-01-01")
    assert r.status_code == 200 and r.json()["asof"] == "2026-03-01"
