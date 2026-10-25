"""Crisis replay and past-performance scorecard (H1-H3, H4/H6), plus decisions and alert centre (G4/G11).

Truth sheets are used only in `scorecard()` and `timeline(reveal=True)`, both explicit evaluation views."""
from __future__ import annotations

import json
import threading
from ..settings import cache_dir
from .util import clean
import time

import numpy as np
import pandas as pd

from ..actions import roles as RL
from ..actions import store as ST
from ..eval import replay as RP
from ..inventory.position import stock_position, stockout_distribution
from ..supply import pulp as PU
from .service import AWARENESS, Service
from .views_control import actions_state

REPLAY_START = pd.Timestamp("2026-03-01")
REPLAY_END = pd.Timestamp("2026-06-30")


class ReplayCache:
    def __init__(self, svc: Service):
        self.svc = svc
        self.rows: dict[str, dict] = {}
        self.thread: threading.Thread | None = None
        self.total = int((REPLAY_END - REPLAY_START).days) + 1
        self.lock = threading.Lock()
        self.file = cache_dir() / f"replay_rows_{svc.ds.params.config_hash}.json"
        try:
            self.rows = json.loads(self.file.read_text(encoding="utf8"))
        except Exception:                                                    # noqa: BLE001
            pass

    def ensure_started(self) -> None:
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._work, daemon=True)
                self.thread.start()

    def _work(self) -> None:
        d = REPLAY_START
        while d <= REPLAY_END:
            key = str(d.date())
            if key not in self.rows:
                try:
                    self.rows[key] = RP.replay_row(self.svc.run(d), "DP")
                except Exception as e:                                   # noqa: BLE001
                    self.rows[key] = {"asof": key, "error": f"{type(e).__name__}: {e}"}
            d += pd.Timedelta(days=1)
        try:
            self.file.write_text(json.dumps(clean(self.rows)), encoding="utf8")
        except Exception:                                                    # noqa: BLE001
            pass


_RC: ReplayCache | None = None


def replay_cache(svc: Service) -> ReplayCache:
    global _RC
    if _RC is None:
        _RC = ReplayCache(svc)
    return _RC


def timeline(svc: Service, reveal: bool = False) -> dict:
    rc = replay_cache(svc)
    rc.ensure_started()
    rows = [rc.rows[k] for k in sorted(rc.rows)]
    if reveal:
        rows = RP.with_truth(rows, "DP")
    marks = [{"date": str(r.date.date()), "type": r.event_type, "label": r.description} for r in svc.ds.refs["R06"].itertuples()]
    return {"rows": rows, "done": len(rc.rows), "total": rc.total, "complete": len(rc.rows) >= rc.total, "events": marks,
            "awareness": str(AWARENESS.date()), "signal": "UNITS_SOLD_SIGNAL",
            "note": "Each row was produced by an engine run that could only see data arrived by that date. Event markers are replay labels from the workbook.",
            "provenance": ["SYNTHETIC"] + (["TRUTH (evaluation view)"] if reveal else [])}


def series(svc: Service, day, flavor: str = "DP", region: str = "ALL", reveal: bool = False) -> dict:
    run = svc.run(day)
    out = RP.qc_series_view(run, flavor, region)
    if reveal:
        from ..data.truth import Truth
        t = Truth().qc_true()
        t = t[t.flavor_id == flavor]
        if region != "ALL":
            t = t[t.region_id == region]
        g = t.groupby("date")["demand_true"].sum()
        out["truth"] = [{"date": str(k.date()), "demand_true": float(v)} for k, v in g.items()]
    out["asof"] = str(run.day.date())
    return out


def state(svc: Service, day) -> dict:
    run = svc.run(day)
    ds = svc.ds
    so = run.snap.stockout_log
    first = so[so.flavor_id == "DP"].groupby("depot_id")["start_date"].min()
    ev = [{"date": str(r.date.date()), "type": r.event_type, "label": r.description} for r in ds.refs["R06"].itertuples() if r.date <= run.day]
    route = PU.route_state(run)
    air = run.snap.air_freight
    return {"asof": str(run.day.date()), "events_so_far": ev, "dp_first_stockouts": {k: str(v.date()) for k, v in first.items()},
            "route": route, "air_freight_booked": float(air["cost_inr"].sum()) if len(air) else 0.0,
            "aware_as_was": bool(run.day >= AWARENESS), "awareness_date": str(AWARENESS.date())}


# ---------------------------------------------------------------------------------------------------------
def scorecard(svc: Service) -> dict:
    """H4/H6: last summer's results against the agent's path. Three kinds of number, never mixed: case facts, synthetic replay
    results (workbook logs), and model estimates. Truth is used only for the detection and loss comparisons labelled TRUTH."""
    ds = svc.ds
    import time
    while not timeline(svc)["complete"]:             # the scorecard reads every replay date, so wait for all of them
        time.sleep(2)
    tl = timeline(svc)["rows"]
    df = pd.DataFrame([r for r in tl if "error" not in r])
    from ..data.truth import Truth
    tr = Truth()
    flag = tr.flag_truth()
    lab = tr.label_events()
    spk = lab[lab.event_id.str.endswith("SPK1")].iloc[0] if len(lab[lab.event_id.str.endswith("SPK1")]) else None

    def first_date(col: str) -> str | None:
        s = df[col].dropna() if col in df else pd.Series(dtype=object)
        return None if s.empty else str(s.iloc[0])

    detection = {
        "engine": {"first_watch": first_date("first_watch"), "first_alert": first_date("first_alert"), "first_critical": first_date("first_critical")},
        "workbook_labels": {"first_watch": None if spk is None else str(spk["first_watch_date"]), "first_alert": None if spk is None else str(spk["first_alert_date"]),
                            "first_critical": None if spk is None else str(spk["first_critical_date"])},
        "as_was_planner_awareness": str(AWARENESS.date()),
        "label_note": "Workbook labels come from the injected multiplier (hidden truth). The engine sees units sold only, so after shelves empty it cannot reach the 3x line.",
    }
    ea = detection["engine"]["first_alert"]
    detection["days_before_awareness"] = None if ea is None else int((AWARENESS - pd.Timestamp(ea)).days)
    # first DP stockout
    so = ds.facts["stockout_log"]
    dp_so = so[(so.flavor_id == "DP")]
    first_so = dp_so["start_date"].min()
    detection["first_dp_stockout"] = str(first_so.date())
    detection["alert_vs_first_stockout_days"] = None if ea is None else int((first_so - pd.Timestamp(ea)).days)
    # pulp
    pf = df[["asof", "pf_tier_if_21d_closure", "route_status", "pf_runway_days"]].copy() if len(df) else pd.DataFrame()
    first_crit = None
    if len(pf):
        m = pf[(pf.pf_tier_if_21d_closure == "CRITICAL")]
        first_crit = None if m.empty else str(m.iloc[0]["asof"])
    air = ds.facts["air_freight"]
    closure_start = pd.Timestamp("2026-05-04")
    pulp = {"engine_first_critical_if_21d_closure": first_crit,
            "closure_start": str(closure_start.date()), "first_air_booking_as_was": str(air["booking_date"].min().date()) if len(air) else None,
            "lead_days_before_closure": None if first_crit is None else int((closure_start - pd.Timestamp(first_crit)).days),
            "lead_days_before_air_booking": None if first_crit is None or air.empty else int((air["booking_date"].min() - pd.Timestamp(first_crit)).days),
            "air_freight_as_was_inr": float(air["cost_inr"].sum()) if len(air) else None, "note": "Engine CRITICAL means runway shorter than a 21-day closure plus 3 transit days."}
    # losses: three separate kinds of number
    so_s = so[so.in_summer.astype(bool)]
    wo = ds.facts["writeoff_discount"]
    revenue_per_bottle = ds.params["price"]
    lost_units_logged = float(so_s["lost_units_est"].sum())
    case = {"lost_revenue_share": 0.08, "lost_revenue_cr_assumed": 0.08 * 576, "lost_bottles_cr": 0.768, "writeoff_discount_cr": 6.0, "air_freight_cr": 3.2,
            "label": "CASE (stated in the brief)"}
    replay = {"logged_lost_units": lost_units_logged, "logged_lost_revenue_cr": lost_units_logged * revenue_per_bottle / 1e7,
              "writeoff_discount_cr": float(wo["cost_inr"].sum() / 1e7), "air_freight_cr": pulp["air_freight_as_was_inr"] / 1e7 if pulp["air_freight_as_was_inr"] else None,
              "label": "SYNTHETIC replay (workbook logs: stockout log, discount log, air freight log)"}
    run_end = svc.run(pd.Timestamp("2026-06-30"))
    from ..engine.lost_demand import lost_demand
    est = [r for r in lost_demand(run_end, n=500) if r["channel"] == "ALL"]
    eng = {"label": "MODEL ESTIMATE at 2026-06-30 (censored days only, summed paths)",
           "by_flavor": {r["flavor"]: {"p10": r["lost_p10"], "p50": r["lost_p50"], "p90": r["lost_p90"]} for r in est},
           "total_p50": float(sum(r["lost_p50"] for r in est)), "note": "Estimates only the first part of the loss: the engine sees capped sales, so it under-reads a demand spike that empties shelves."}
    dem = tr.demand_daily()
    dem = dem[(dem.date >= "2026-03-01") & (dem.date <= "2026-06-30")]
    truth = {"label": "TRUTH (hidden synthetic demand, evaluation only)", "lost_units_by_flavor": dem.groupby("flavor_id")["lost"].sum().to_dict(), "lost_units_total": float(dem["lost"].sum())}
    inside = {fl: bool(eng["by_flavor"][fl]["p10"] <= truth["lost_units_by_flavor"].get(fl, 0.0) <= eng["by_flavor"][fl]["p90"]) for fl in eng["by_flavor"]}
    eng["truth_inside_p10_p90"] = inside
    # stockout date predictor against what happened
    pred = []
    first_by_depot = dp_so.groupby("depot_id")["start_date"].min()
    for d in ("2026-03-18", "2026-03-20", "2026-03-22"):
        run = svc.run(d)
        for dp in ds.depots:
            if dp not in first_by_depot.index:
                continue
            pos = stock_position(run, "DP", dp, 300)
            so_d = stockout_distribution(run, "DP", dp, 60, 300, pos=pos)
            actual = first_by_depot[dp]
            pred.append({"asof": d, "depot": dp, "p10": so_d["date_p10"], "p50": so_d["date_p50"], "actual_first_stockout": str(actual.date()),
                         "error_p50_days": int((pd.Timestamp(so_d["date_p50"]) - actual).days), "p10_before_actual": bool(pd.Timestamp(so_d["date_p10"]) <= actual)})
    pdf = pd.DataFrame(pred)
    predictor = {"rows": pred, "median_abs_error_p50_days": float(pdf.groupby("asof")["error_p50_days"].apply(lambda s: s.abs().median()).mean()) if len(pdf) else None,
                 "p10_before_actual_share": float(pdf["p10_before_actual"].mean()) if len(pdf) else None}
    # model-estimated potential on key dates
    potential = []
    from . import views_control as VC
    from ..finance.impact import waterfall
    for d in ("2026-03-24", "2026-05-04"):
        run = svc.run(d)
        from ..actions import generator as GEN
        recs = GEN.generate(run)
        wf = waterfall(run, [{"status": "PENDING", **r} for r in recs], "margin", only_open=False)
        potential.append({"asof": d, "actions": len(recs), "net_saved_margin": wf["net_saved"], "do_nothing_loss": wf["do_nothing"]["total"],
                          "mitigation_cost": wf["actions"]["mitigation_total"], "loss_avoided": wf["actions"]["loss_avoided"]})
    return {"detection": detection, "pulp": pulp, "case": case, "replay": replay, "engine": eng, "truth": truth, "predictor": predictor, "potential": potential,
            "potential_note": "Per-date estimates for a 28-day window if every recommendation raised that day were approved as written. Windows overlap, so do not add them. Model estimates under uncalibrated scenario intervals, not measured savings.",
            "provenance": ["SYNTHETIC", "ESTIMATED", "TRUTH (evaluation only)"]}


def _saved(svc: Service, day: str, types: tuple[str, ...] | None = None) -> dict:
    """Revenue basis, 28-day window: if every recommendation raised on `day` were approved as written (model estimate)."""
    from ..actions import generator as GEN
    from ..finance.impact import waterfall
    run = svc.run(pd.Timestamp(day))
    recs = [r for r in GEN.generate(run) if types is None or r["type"] in types]
    wf = waterfall(run, [{"status": "PENDING", **r} for r in recs], "revenue", only_open=False)
    return {"date": day, "actions": len(recs), "loss_if_nobody_acts": wf["do_nothing"]["total"], "loss_avoided": wf["actions"]["loss_avoided"],
            "cost_of_acting": wf["actions"]["mitigation_total"], "net_saved": wf["net_saved"]}


def _exposure(svc: Service, day: str, rec_id: str) -> float | None:
    from ..actions import generator as GEN
    for r in GEN.generate(svc.run(pd.Timestamp(day))):
        if r["id"] == rec_id:
            return None if r.get("exposure_inr") is None else float(r["exposure_inr"])
    return None


def scenarios(svc: Service) -> dict:
    """One row per crisis scenario: when it happened, when the agent saw it, and the revenue effect where it can be estimated.
    The agent's dates come from replay runs that only saw data arrived by each date. Savings are model estimates."""
    while not timeline(svc)["complete"]:
        time.sleep(2)
    ds = svc.ds
    rows = [r for r in timeline(svc)["rows"] if "error" not in r]
    df = pd.DataFrame(rows)
    ev: dict[str, str] = {}
    for r in ds.refs["R06"].itertuples():
        ev.setdefault(r.event_type, str(r.date.date()))
    last = df.iloc[-1]
    def first_non_null(col):
        v = df[col].dropna()
        return None if v.empty else str(v.iloc[0])

    watch, alert = first_non_null("first_watch"), first_non_null("first_alert")

    def short(d):
        t = pd.Timestamp(d)
        return f"{t.day} {t.strftime('%b')}"
    awareness = str(AWARENESS.date())

    def days(a, b):
        return None if (a is None or b is None) else int((pd.Timestamp(a) - pd.Timestamp(b)).days)

    out = []
    first_day = str(df.iloc[0]["asof"])
    out.append({"id": "launch", "title": "New flavor launch",
                "happened": {"date": ev.get("launch"), "text": "Dragon Passion launched. Marketing told procurement on " + short(ev.get("launch notice")) + "."},
                "model": {"date": first_day, "text": "A forecast was ready from the first replay day, built from the two most similar flavors, before any sales."},
                "lead_days": days(ev.get("launch"), first_day), "lead_vs": "launch day", "saved": None, "at_stake_inr": None})

    out.append({"id": "surge", "title": "Viral reel and demand surge",
                "happened": {"date": ev.get("influencer"), "text": "A 30-second reel went viral. Planners noticed the shortage on " + short(awareness) + "."},
                "model": {"date": alert, "text": f"Surge watch on {short(watch)}, alert on {short(alert)}, from units sold only." if alert else "No alert raised."},
                "lead_days": days(awareness, alert), "lead_vs": "planners noticing", "saved": _saved(svc, alert) if alert else None, "at_stake_inr": None})

    pred = []
    so = ds.facts["stockout_log"]
    first_by_depot = so[so.flavor_id == "DP"].groupby("depot_id")["start_date"].min()
    from ..inventory.position import stock_position, stockout_distribution
    run18 = svc.run("2026-03-18")
    for dp in ds.depots:
        if dp not in first_by_depot.index:
            continue
        sd = stockout_distribution(run18, "DP", dp, 60, 300, pos=stock_position(run18, "DP", dp, 300))
        pred.append({"p10": pd.Timestamp(sd["date_p10"]), "actual": first_by_depot[dp]})
    n_ok = sum(1 for p in pred if p["p10"] <= p["actual"])
    out.append({"id": "stockout", "title": "First stockouts",
                "happened": {"date": ev.get("stockout"), "text": f"Dragon Passion shelves started emptying; {len(pred)} depots ran out within weeks."},
                "model": {"date": "2026-03-18", "text": f"Stockout dates were forecast on 18 Mar. The earliest-likely date came on or before the real stockout at {n_ok} of {len(pred)} depots."},
                "lead_days": days(ev.get("stockout"), "2026-03-18"), "lead_vs": "the first stockout", "saved": None, "at_stake_inr": None,
                "note": "Money for this scenario is counted under the surge."})

    pcrit = df[df["pf_tier_if_21d_closure"] == "CRITICAL"]
    pdate = None if pcrit.empty else str(pcrit.iloc[0]["asof"])
    out.append({"id": "closure", "title": "Passionfruit highway closes",
                "happened": {"date": ev.get("route closure"), "text": "A landslide closed the highway for 21 days."},
                "model": {"date": pdate, "text": "Passionfruit pulp runway was shorter than a 21-day closure plus transit, and a pulp buffer was recommended."},
                "lead_days": days(ev.get("route closure"), pdate), "lead_vs": "the closure", "saved": None,
                "at_stake_inr": _exposure(svc, pdate, "pulp_buffer:PP:-") if pdate else None,
                "note": "At stake is the sales exposure the buffer addresses. It is not counted as saved."})

    air = ds.facts["air_freight"]
    bill = float(air["cost_inr"].sum()) if len(air) else None
    out.append({"id": "air", "title": "Air freight booked at a premium",
                "happened": {"date": ev.get("air freight"), "text": "Air freight was booked in closure week 2" + (f", bill {bill / 1e7:.1f} crore" if bill else "") + "."},
                "model": {"date": pdate, "text": "The pulp buffer warning stood well before the booking, so freight could have been ordered earlier and cheaper."},
                "lead_days": days(ev.get("air freight"), pdate), "lead_vs": "the air booking", "saved": None, "at_stake_inr": bill,
                "note": "At stake is the air freight bill paid. It is not counted as saved."})

    later = df[(df["asof"] >= "2026-04-21") & (df["ratio_last_uncensored"] >= 1.5) & (~df["censored_today"].astype(bool))]
    rdate = None if later.empty else str(later.iloc[0]["asof"])
    out.append({"id": "rival", "title": "Sales climb again as a rival launches",
                "happened": {"date": ev.get("competitor"), "text": "A rival launched a dragonfruit-passionfruit drink."},
                "model": {"date": rdate, "text": "Sales ran well above the baseline again once shelves refilled, and the agent flagged it."},
                "lead_days": days(ev.get("competitor"), rdate), "lead_vs": "the rival launch", "saved": _saved(svc, rdate) if rdate else None, "at_stake_inr": None})

    out.append({"id": "cut", "title": "Production moved to protect Dragon Passion",
                "happened": {"date": ev.get("production cut"), "text": "Punch production was cut to free line time for Dragon Passion."},
                "model": {"date": "2026-03-01", "text": "Giving Dragon Passion more line time from existing capacity was recommended from the first replay day."},
                "lead_days": days(ev.get("production cut"), "2026-03-01"), "lead_vs": "the cut", "saved": None,
                "at_stake_inr": _exposure(svc, "2026-03-01", "mix_shift:DP:-"),
                "note": "At stake is the sales exposure of that recommendation. It is not counted as saved."})
    lost = float(so[so.in_summer.astype(bool)]["lost_units_est"].sum()) * ds.params["price"]
    return {"scenarios": out, "awareness": awareness, "company_lost_revenue_inr": lost, "case_lost_revenue_inr": 0.08 * 576 * 1e7,
            "note": "Dates for the agent come from replays that saw only data arrived by that day. Money is a model estimate on a revenue basis: every recommendation raised that day, approved as written, over the next 28 days. Windows overlap, so do not add them. Nothing here is a measured result.",
            "provenance": ["SYNTHETIC", "ESTIMATED"]}


# ---------------------------------------------------------------------------------------------------------
def alerts_view(svc: Service, day) -> dict:
    """G4 / G11: one card per cause. Duplicate alerts (the same flavor and kind across depots) merge into a single card."""
    acts = actions_state(svc, day)
    pend = [a for a in acts if a["status_at_asof"] == "PENDING" and a["active"]]
    groups: dict[str, dict] = {}
    for a in pend:
        gk = a.get("group_key") or a["id"]
        g = groups.setdefault(gk, {"group_key": gk, "flavor": a["flavor"], "flavor_name": a["flavor_name"], "kinds": set(), "items": [], "depots": [], "exposure": 0.0,
                                  "cost": 0.0, "qty": 0.0, "deadline": a["deadline"], "approvers": set()})
        g["items"].append(a["id"])
        g["kinds"].add(a["type"])
        if a["depot"]:
            g["depots"].append(a["depot_name"])
        g["exposure"] += a["exposure_inr"] or 0.0
        g["cost"] += a["cost_inr"] or 0.0
        g["qty"] += a["qty"] or 0.0
        g["approvers"].add(a["approver_role"])
        if a["deadline"] and (g["deadline"] is None or a["deadline"] < g["deadline"]):
            g["deadline"] = a["deadline"]
        esc = a["escalation"] or {}
        g["owner"] = esc.get("current_owner", a["approver_role"])
        g["escalated"] = g.get("escalated", False) or esc.get("escalated", False)
        g["overdue"] = g.get("overdue", False) or esc.get("deadline_passed", False)
        g["hours_to_next"] = esc.get("hours_to_next_escalation")
    out = []
    for g in groups.values():
        g["kinds"] = sorted(g["kinds"])
        g["approvers"] = sorted(g["approvers"])
        g["merged_count"] = len(g["items"])
        out.append(g)
    out.sort(key=lambda g: -g["exposure"])
    return {"asof": str(svc.run(day).day.date()), "groups": out, "pending": len(pend), "sla_hours": svc.ds.params["approval_sla_hours"],
            "sla_note": "Approval response time (24 hours per level) is ASSUMED; escalation follows the workbook's role chain.",
            "provenance": ["CALCULATED", "ASSUMED"]}


def decide(svc: Service, action_id: str, decision: str, actor: str, day, reason: str | None, qty: float | None) -> dict:
    a = ST.get_action(action_id)
    if a is None:
        raise KeyError(action_id)
    if not RL.can_decide(svc.ds, actor, a["approver_role"]):
        raise PermissionError(f"{actor} cannot decide this item; it belongs to {a['approver_role']} or anyone above them in the chain.")
    if decision == "modify" and (qty is None or qty < 0):
        raise ValueError("Modify needs a quantity.")
    if decision in ("reject", "modify") and not (reason and reason.strip()):
        raise ValueError("A reason is required to reject or modify.")
    asof = str(pd.Timestamp(day).date())
    return ST.decide(action_id, decision, actor, asof, reason, qty)


def history_log(svc: Service) -> dict:
    """The workbook's prior decision log (SYNTHETIC demo), kept apart from live decisions."""
    d = svc.ds.facts["decisions_log"]
    return {"rows": d.assign(decided_at=d["decided_at"].astype(str)).to_dict("records"), "label": "SYNTHETIC (demo) prior decisions from the workbook; not part of the live register."}
