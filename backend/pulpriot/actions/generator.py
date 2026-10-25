"""Turn engine state into recommendations. Each one is PENDING until a named stakeholder decides; nothing is executed.

Rules (each recommendation carries the numbers behind it and the as-of snapshot it rests on):
  reforecast      national QC spike or drop event open at ALERT or higher
  rebalance/plant/copacker   a RED depot-flavor cell: best on-time option from the mitigation comparison
  fefo_redirect   MT/QC-eligible stock that will not sell before the 60-day cutoff
  air_freight / pulp_buffer  passionfruit runway shorter than a closure window
  mix_shift       existing line time moved from over-covered flavors to urgent ones (COO: peak trade-off)
  allocation_reset  depots whose demand share has moved away from the monthly allocation"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from ..engine.forecast import spike_rows
from ..inventory.mitigation import mitigation_options
from ..inventory.policy import fefo_plan, lots_now, sellable_clock
from ..inventory.position import stock_position
from ..inventory.risk import demand_shift, risk_grid
from ..settings import MODEL_VERSION
from ..supply import capacity as CA
from ..supply import pulp as PU
from . import roles as RL
from . import store as ST


def _id(kind: str, flavor: str | None, depot: str | None, asof: str) -> str:
    """One open recommendation per (type, flavor, depot). A decided one is not re-raised for 21 days."""
    base = f"{kind}:{flavor or '-'}:{depot or '-'}"
    for a in reversed(ST.list_actions()):
        if a["id"] == base or a["id"].startswith(base + "@"):
            if a["status"] == "PENDING":
                return a["id"]
            if a["decided_asof"] and (pd.Timestamp(asof) - pd.Timestamp(a["decided_asof"])).days < 21:
                return a["id"]
    existing = ST.get_action(base)
    return base if existing is None else f"{base}@{asof}"


def _unit(run) -> float:
    return run.params["price"] * run.params["margin"]


def _discount_cost_per_bottle(run) -> float:
    w = run.snap.writeoff_discount
    if w.empty:
        return run.params["price"] * 0.4
    d = w[w.type == "discount"]
    return float(d["cost_inr"].sum() / d["bottles"].sum()) if len(d) and d["bottles"].sum() > 0 else run.params["price"] * 0.4


def generate(run) -> list[dict]:
    key = ("recs",)
    cache = run.__dict__.setdefault("_pcache", {})
    if key in cache:
        return cache[key]
    names = run.ds.flavor_name()
    dn = run.ds.depot_name()
    asof = str(run.day.date())
    recs: list[dict] = []
    snap_base = {"asof": asof, "arrival_cutoff": str(run.ts), "model_version": MODEL_VERSION, "config_hash": run.params.config_hash,
                 "provenance": "SYNTHETIC replay snapshot"}
    unit = _unit(run)

    # ---- 1. demand events ------------------------------------------------------------------------------------
    for r in spike_rows(run, region="ALL"):
        if r["stage"] in ("NORMAL", "WATCH", "REVERTED") or r["tier"] not in ("ALERT", "CRITICAL"):
            continue
        ex = r.get("extra_bottles", {}).get("28")
        if not ex:
            continue
        up = r["direction"] == "UP"
        fl = r["flavor"]
        rev, mar = abs(ex["p50"]) * run.params["price"], abs(ex["p50"]) * unit
        title = (f"{names[fl]} is running at {r['ratio_R']:.1f}x its baseline: re-forecast and raise the plan" if up else
                 f"{names[fl]} is running below its baseline: re-forecast and slow the plan")
        recs.append({"id": _id("reforecast", fl, None, asof), "type": "reforecast", "flavor": fl, "depot": None, "qty": abs(ex["p50"]), "unit": "bottles",
                     "title": title, "rationale": f"Quick-commerce daily sales crossed the alert line on {r['first_dates'].get('ALERT') or r['last_data_day']} (units sold signal, stockout days excluded). "
                     f"Scenario mixture puts the next 28 days {'above' if up else 'below'} baseline by about {abs(ex['p50']):,.0f} bottles (P10 {ex['p10']:,.0f}, P90 {ex['p90']:,.0f}).",
                     "source_snapshot": {**snap_base, "ratio": r["ratio_R"], "stage": r["stage"], "tier": r["tier"], "weights": r.get("weights")},
                     "exposure_inr": mar, "cost_inr": 0.0, "net_benefit_inr": None, "deadline": str((run.day + pd.Timedelta(days=1)).date()),
                     "approver_role": "ROLE_PLN", "group_key": f"event:{fl}",
                     "payload": {"exposure_revenue": rev, "exposure_margin": mar, "interval_type": r.get("interval_type"), "warnings": r["warnings"]}})

    # ---- 2. stockout risk -> best on-time option -----------------------------------------------------------------
    grid = [c for c in risk_grid(run) if c["status"] == "RED"]
    grid.sort(key=lambda c: (c["days_p10"], -c["stock_p50"]))
    pos_cache: dict = {}
    cons: dict = {"fg_used": {}, "donor_used": {}, "copacker_used": 0.0}
    for c in grid[:14]:
        fl, dp = c["flavor"], c["depot"]
        m = mitigation_options(run, fl, dp, n=200, pos_cache=pos_cache, constraints=cons)
        feas = [o for o in m["options"] if o["key"] != "nothing" and o["feasible"] and o["qty"] > 0]
        if not feas:
            exposure = m["lost_if_nothing"]["mean"] * unit
            recs.append({"id": _id("rebalance", fl, dp, asof), "type": "no_option", "flavor": fl, "depot": dp, "qty": m["need_bottles"], "unit": "bottles",
                         "title": f"{names[fl]} at {dn[dp]}: stockout expected {m['stockout']['date_p10']}, and no option can deliver",
                         "rationale": "Transfer donors are at their minimum cover, plants hold no finished stock of this flavor, and the co-packer has no headroom.",
                         "source_snapshot": {**snap_base, "stockout": m["stockout"]}, "exposure_inr": exposure, "cost_inr": 0.0, "net_benefit_inr": None,
                         "deadline": str(run.day.date()), "approver_role": "ROLE_SCH", "group_key": f"stockout:{fl}", "payload": {"options": m["options"]}})
            continue
        on_time = [o for o in feas if not o["arrives_too_late"]]
        pool = on_time or feas
        best = max(pool, key=lambda o: o["net_value_mean"])
        kind = {"transfer": "rebalance", "plant": "plant", "copacker": "copacker"}[best["key"]]
        if best["key"] == "plant":
            cons["fg_used"][fl] = cons["fg_used"].get(fl, 0.0) + best["qty"]
        elif best["key"] == "transfer":
            dk = (fl, best["detail"].get("donor"))
            cons["donor_used"][dk] = cons["donor_used"].get(dk, 0.0) + best["qty"]
        else:
            cons["copacker_used"] += best["qty"]
        arr_lag = (pd.Timestamp(best["arrival_date"]) - run.day).days
        p10 = pd.Timestamp(m["stockout"]["date_p10"])
        decide_by = p10 - pd.Timedelta(days=arr_lag)
        exposure = m["lost_if_nothing"]["mean"] * unit
        verb = {"rebalance": f"Move {best['qty']:,.0f} bottles from {dn[best['detail'].get('donor', 'DEP01')] if best['detail'].get('donor') else 'a donor depot'}",
                "plant": f"Ship {best['qty']:,.0f} bottles from {best['detail'].get('plant', 'the plant')} stock",
                "copacker": f"Order {best['qty']:,.0f} bottles from the co-packer"}[kind]
        recs.append({"id": _id(kind, fl, dp, asof), "type": kind, "flavor": fl, "depot": dp, "qty": best["qty"], "unit": "bottles",
                     "title": f"{verb} to {dn[dp]} ({names[fl]})",
                     "rationale": f"Earliest-likely stockout {m['stockout']['date_p10']} (expected {m['stockout']['date_p50']}). {best['label']} arrives {best['arrival_date']} "
                                  f"({'before' if not best['arrives_too_late'] else 'after'} the earliest-likely stockout). {best['basis']}.",
                     "source_snapshot": {**snap_base, "stockout": m["stockout"], "option": {k: best[k] for k in ("key", "qty", "arrival_date", "cost_per_bottle")}},
                     "exposure_inr": exposure, "cost_inr": best["cost_total"], "net_benefit_inr": best["net_value_mean"],
                     "deadline": str(decide_by.date()), "approver_role": RL.approver_for(kind, best["cost_total"]), "group_key": f"stockout:{fl}",
                     "payload": {"options": m["options"], "late": bool(best["arrives_too_late"]), "need": m["need_bottles"], "interval_type": m["interval_type"]}})

    # ---- 3. shelf-life: bottles that will miss the MT/QC cutoff ----------------------------------------------------------
    disc = _discount_cost_per_bottle(run)
    full = risk_grid(run)
    cand = sorted([c for c in full if c["excess"] or (c["simple_cover_days"] or 0) > 25], key=lambda c: -c["stock_p50"])[:10]
    for c in cand:
        fl, dp = c["flavor"], c["depot"]
        clock = sellable_clock(run, fl, dp)
        if clock["total_qty"] <= 0 or clock["at_risk_mtqc"] < max(20000, 0.15 * clock["total_qty"]):
            continue
        recs.append({"id": _id("fefo_redirect", fl, dp, asof), "type": "fefo_redirect", "flavor": fl, "depot": dp, "qty": clock["at_risk_mtqc"], "unit": "bottles",
                     "title": f"Ship oldest {names[fl]} lots first at {dn[dp]}: {clock['at_risk_mtqc']:,.0f} bottles will miss the MT/QC cutoff",
                     "rationale": f"The oldest MT/QC-eligible lot has {clock['oldest_eligible_days_left']} days left. At the current MT+QC rate (about {clock['mtqc_rate_per_day']:,.0f}/day) "
                                  f"these bottles cannot sell to MT or QC in time; they would go to GT at a discount (about INR {disc:.0f}/bottle in the discount log).",
                     "source_snapshot": {**snap_base, "buckets": clock["buckets"]}, "exposure_inr": clock["at_risk_mtqc"] * disc, "cost_inr": 0.0, "net_benefit_inr": None,
                     "deadline": str((run.day + pd.Timedelta(days=max(clock["oldest_eligible_days_left"] - 2, 0))).date()),
                     "approver_role": "ROLE_SAL", "group_key": f"shelf:{fl}", "payload": {"fefo": fefo_plan(run, fl, dp)["plan"][:6]}})

    # ---- 4. passionfruit pulp ----------------------------------------------------------------------------------------------
    stat = {s["ingredient"]: s for s in PU.pulp_status(run, 200)}
    pf = stat["ING_PF"]
    route = pf["route"] or {}
    if pf["runway_days"] is not None:
        if route.get("status") == "closed":
            remain = max(21 - int(route.get("closure_day", 0)), 1)
            ex = PU.closure_exposure(run, remain)
            if ex["kg_short"] > 0:
                recs.append({"id": _id("air_freight", "PP", None, asof), "type": "air_freight", "flavor": "PP", "depot": None, "qty": ex["kg_short"], "unit": "kg",
                             "title": f"Highway closed: book about {ex['kg_short']:,.0f} kg of passionfruit pulp by air",
                             "rationale": f"Runway is {ex['runway_days']:.0f} days against about {remain} days of closure left (planning length 21 days, ASSUMED) plus 3 days road transit. "
                                          f"Air freight at INR {ex['air_rate']:.0f}/kg is five times the normal rate.",
                             "source_snapshot": {**snap_base, "route": route}, "exposure_inr": ex["air_cost_inr"] - ex["normal_cost_inr"], "cost_inr": ex["air_cost_inr"],
                             "net_benefit_inr": None, "deadline": str(run.day.date()), "approver_role": RL.approver_for("air_freight", ex["air_cost_inr"]), "group_key": "pulp:PF",
                             "payload": {"exposure": ex}})
        else:
            ex = PU.closure_exposure(run, 21)
            if ex["kg_short"] > 0:
                recs.append({"id": _id("pulp_buffer", "PP", None, asof), "type": "pulp_buffer", "flavor": "PP", "depot": None, "qty": ex["kg_short"], "unit": "kg",
                             "title": f"Build a passionfruit buffer: runway is {ex['runway_days']:.0f} days, a repeat of last summer's 21-day closure needs {ex['closure_days'] + ex['transit_days']}",
                             "rationale": f"The highway closed for a week or more in {PU.corridor(run)['lane1']['history_long_closures']} of the last "
                                          f"{PU.corridor(run)['lane1']['history_years']} summers (highway log) and there is no alternative road. Short by about {ex['kg_short']:,.0f} kg.",
                             "source_snapshot": {**snap_base, "route": route}, "exposure_inr": ex["air_cost_inr"] - ex["normal_cost_inr"], "cost_inr": None, "net_benefit_inr": None,
                             "deadline": str((run.day + pd.Timedelta(days=3)).date()), "approver_role": "ROLE_PRC", "group_key": "pulp:PF", "payload": {"exposure": ex}})

    # ---- 5. production mix ------------------------------------------------------------------------------------------------------
    rp = CA.reprioritiser(run)
    inc = [m for m in rp["moves"] if m["action"] == "INCREASE" and m["bottles_per_week"] > 5000]
    for m in inc[:2]:
        fl = m["flavor"]
        recs.append({"id": _id("mix_shift", fl, None, asof), "type": "mix_shift", "flavor": fl, "depot": None, "qty": m["bottles_per_week"], "unit": "bottles/week",
                     "title": f"Give {names[fl]} {m['bottles_per_week']:,.0f} more bottles a week from existing line time",
                     "rationale": f"National cover is {m['cover_days']:.0f} days. Capacity comes from over-covered flavors and the 5% utilisation headroom; plants cannot add lines.",
                     "source_snapshot": {**snap_base, "cover_days": m["cover_days"]}, "exposure_inr": m["bottles_per_week"] * 4 * unit, "cost_inr": 0.0, "net_benefit_inr": None,
                     "deadline": str((run.day + pd.Timedelta(days=2)).date()), "approver_role": "ROLE_COO", "group_key": f"mix:{fl}", "payload": {"moves": rp["moves"]}})

    # ---- 6. allocation drift ------------------------------------------------------------------------------------------------------
    sh = [s for s in demand_shift(run) if s["flag"] == "UNDER_SUPPLIED"]
    sh.sort(key=lambda s: -s["shift_pp"])
    for s in sh[:2]:
        fl, dp = s["flavor"], s["depot"]
        recs.append({"id": _id("allocation_reset", fl, dp, asof), "type": "allocation_reset", "flavor": fl, "depot": dp, "qty": None, "unit": "pp",
                     "title": f"Reset the {names[fl]} allocation toward {dn[dp]}: demand share {s['demand_share'] * 100:.1f}% against {s['allocation_share'] * 100:.1f}% planned",
                     "rationale": "Allocations are reset monthly on last year's mix; observed demand (censor-adjusted) has moved away from it.",
                     "source_snapshot": {**snap_base, "shift_pp": s["shift_pp"]}, "exposure_inr": None, "cost_inr": 0.0, "net_benefit_inr": None,
                     "deadline": str((run.day + pd.Timedelta(days=5)).date()), "approver_role": "ROLE_PLN", "group_key": f"alloc:{fl}", "payload": s})

    for r in recs:
        r["model_version"] = MODEL_VERSION
        r["config_hash"] = run.params.config_hash
    cache[key] = recs
    return recs


def sync(run) -> list[dict]:
    """Generate, persist new ones as PENDING, and return the stored rows (decided rows keep their decisions)."""
    asof = str(run.day.date())
    out = []
    for r in generate(run):
        out.append(ST.upsert_recommendation(r, asof))
    return out
