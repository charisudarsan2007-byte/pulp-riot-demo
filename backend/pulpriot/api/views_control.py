"""Control tower views: executive summary (A3), top actions (A6), risk heatmap (A4), supply-chain pulse Sankey (A9),
rebalancing lanes (A8), quick stock checker (A2), weekly review one-pager (A7)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..actions import generator as GEN
from ..actions import roles as RL
from ..actions import store as ST
from ..data.loader import FLAVORS
from ..engine.forecast import get_forecast, spike_rows
from ..inventory.mitigation import mitigation_options
from ..inventory.policy import safety_stock
from ..inventory.position import stock_position, stockout_distribution
from ..inventory.risk import risk_grid
from ..supply import pulp as PU
import functools
import threading

from .service import Service

_LOCKS: dict = {}
_GUARD = threading.Lock()


def serial(fn):
    """One heavy computation per replay date at a time: concurrent identical requests wait and then hit the caches."""
    @functools.wraps(fn)
    def wrapper(svc, day, *a, **k):
        key = str(pd.Timestamp(day).date())
        with _GUARD:
            lock = _LOCKS.setdefault(key, threading.RLock())
        with lock:
            return fn(svc, day, *a, **k)
    return wrapper

CITY_XY = {"DEP01": (77.21, 28.61), "DEP02": (75.79, 26.91), "DEP03": (80.95, 26.85), "DEP04": (72.88, 19.08), "DEP05": (72.57, 23.02),
           "DEP06": (77.59, 12.97), "DEP07": (78.49, 17.39), "DEP08": (88.36, 22.57), "DEP09": (91.74, 26.14)}


@serial
def grid(svc: Service, day) -> list[dict]:
    run = svc.run(day)
    cache = run.__dict__.setdefault("_pcache", {})
    if ("grid",) not in cache:
        cache[("grid",)] = risk_grid(run)
    return cache[("grid",)]


@serial
def actions_state(svc: Service, day, sync: bool = True) -> list[dict]:
    """Stored actions visible at the as-of day, with escalation state and whether the trigger still holds."""
    run = svc.run(day)
    ds = svc.ds
    active_ids = set()
    if sync:
        for r in GEN.sync(run):
            active_ids.add(r["id"])
    asof = str(run.day.date())
    sla = ds.params["approval_sla_hours"]
    out = []
    names = ds.flavor_name()
    dn = ds.depot_name()
    for a in ST.list_actions(asof):
        a = dict(a)
        decided = a["status"] != "PENDING" and (a["decided_asof"] or "") <= asof
        if not decided:
            a["status_at_asof"] = "PENDING"
        else:
            a["status_at_asof"] = a["status"]
        a["active"] = a["id"] in active_ids
        a["flavor_name"] = names.get(a["flavor"], a["flavor"]) if a["flavor"] else None
        a["depot_name"] = dn.get(a["depot"], a["depot"]) if a["depot"] else None
        a["escalation"] = RL.escalation_state(ds, a, asof, sla) if a["status_at_asof"] == "PENDING" else None
        import json
        try:
            a["payload"] = json.loads(a["payload"]) if isinstance(a["payload"], str) else a["payload"]
            a["source_snapshot"] = json.loads(a["source_snapshot"]) if isinstance(a["source_snapshot"], str) else a["source_snapshot"]
        except Exception:                                                    # noqa: BLE001
            pass
        out.append(a)
    return out


def top_actions(svc: Service, day, economics: str = "margin", k: int = 5) -> list[dict]:
    acts = [a for a in actions_state(svc, day) if a["status_at_asof"] == "PENDING" and a["active"]]
    key = "exposure_margin" if economics == "margin" else "exposure_revenue"

    def val(a: dict) -> float:
        pl = a.get("payload") or {}
        if economics == "revenue" and a["exposure_inr"] and svc.ds.params["margin"] > 0 and a["type"] not in ("air_freight", "pulp_buffer", "fefo_redirect"):
            return pl.get(key, a["exposure_inr"] / svc.ds.params["margin"])
        return a["exposure_inr"] or 0.0

    acts.sort(key=lambda a: -val(a))
    for a in acts:
        a["rank_value"] = val(a)
    return acts[:k]


@serial
def control_tower(svc: Service, day, economics: str = "margin") -> dict:
    run = svc.run(day)
    ds = svc.ds
    g = grid(svc, day)
    names = ds.flavor_name()
    # yesterday's quick-commerce sales against the trailing weekly average
    q = run.snap.qc_daily
    y = run.day - pd.Timedelta(days=1)
    qc_rows = []
    tot_y = tot_avg = 0.0
    for fl in FLAVORS:
        s = run.series.get((fl, "ALL"))
        if s is None or len(s.q) == 0:
            continue
        yy = float(s.q[-1])
        avg = float(np.nanmean(s.q[-8:-1])) if len(s.q) > 7 else yy
        flagged = int(((q.flavor_id == fl) & (q.date == y) & (q.stockout_flag == 1)).sum())
        qc_rows.append({"flavor": fl, "name": names[fl], "yesterday": yy, "avg7": avg, "ratio": yy / avg if avg > 0 else None, "depots_out": flagged,
                        "censored": bool(s.cens[-1] >= 0.5)})
        tot_y += yy
        tot_avg += avg
    red = [c for c in g if c["status"] == "RED"]
    amber = [c for c in g if c["status"] == "AMBER"]
    excess = [c for c in g if c["excess"]]
    pulp = [{k: v[k] for k in ("ingredient", "name", "stock_kg", "daily_use_kg", "runway_days", "inbound_blocked_by_closure", "critical", "route")} for v in PU.pulp_status(run, 150)
            if v["critical"]]
    acts = actions_state(svc, day)
    pend = [a for a in acts if a["status_at_asof"] == "PENDING" and a["active"]]
    esc = [a for a in pend if a["escalation"] and a["escalation"]["escalated"]]
    over = [a for a in pend if a["escalation"] and a["escalation"]["deadline_passed"]]
    spikes = [s for s in spike_rows(run, region="ALL") if s["stage"] not in ("NORMAL", "REVERTED")]
    head = None
    if spikes:
        s = sorted(spikes, key=lambda s: -(abs(np.log(max(s["ratio_R"], 1e-3)))))[0]
        head = headline(run, s, names)
    return {
        "asof": str(run.day.date()), "qc": {"rows": qc_rows, "total_yesterday": tot_y, "total_avg7": tot_avg, "day": str(y.date()),
                                             "platform_split": "Not in the supplied feed (Blinkit, Zepto, Swiggy are not separated)."},
        "stock": {"red": len(red), "amber": len(amber), "green": len(g) - len(red) - len(amber), "excess": len(excess), "cells": len(g),
                  "worst": sorted(red, key=lambda c: c["days_p10"])[:6]},
        "pulp": pulp, "approvals": {"pending": len(pend), "escalated": len(esc), "overdue": len(over)},
        "top_actions": top_actions(svc, day, economics, 5), "headline": head,
        "spikes": [{"flavor": s["flavor"], "name": names[s["flavor"]], "stage": s["stage"], "tier": s["tier"], "ratio": s["ratio_R"]} for s in spikes],
        "provenance": ["SYNTHETIC", "CALCULATED", "ESTIMATED"], "interval_type": "SCENARIO",
    }


def headline(run, s: dict, names: dict) -> dict:
    """One plain line for the largest live demand event (B11), numbers all computed."""
    fl = s["flavor"]
    ratio = s.get("R_p") or s["ratio_R"]
    ex = (s.get("extra_bottles") or {}).get("28")
    rv = (s.get("revenue_at_risk") or {}).get("28")
    up = s.get("event_direction", "UP") == "UP"
    if ex is None:
        return {"flavor": fl, "text": f"{names[fl]} is running at {s['ratio_R']:.1f}x its baseline.", "ratio": s["ratio_R"]}
    pct = (ratio - 1) * 100
    txt = (f"{names[fl]} is selling at {ratio:.1f}x its normal speed ({'+' if pct >= 0 else ''}{pct:.0f}%), "
           f"{'putting about' if up else 'leaving about'} Rs {abs(rv['p50']) / 1e7:.1f} crore of revenue {'at stake' if up else 'unsold'} over the next 28 days "
           f"(P10 {abs(rv['p10']) / 1e7:.1f} to P90 {abs(rv['p90']) / 1e7:.1f} crore, scenario range, not a calibrated interval).")
    return {"flavor": fl, "text": txt, "ratio": ratio, "ratio_basis": s.get("R_basis"), "tier": s["tier"], "stage": s["stage"],
            "extra_bottles": ex, "revenue_at_risk": rv, "shape_unknown": s.get("shape_unknown"), "warnings": s["warnings"]}


@serial
def heatmap(svc: Service, day) -> dict:
    run = svc.run(day)
    g = grid(svc, day)
    ing = []
    stat = {s["ingredient"]: s for s in PU.pulp_status(run, 150)}
    pc = run.snap.pulp_consumption
    st = run.snap.pulp_stock
    d = st["date"].max() if len(st) else None
    route = PU.route_state(run)
    for i in ("ING_PF", "ING_DF"):
        for plant in ("PLT1", "PLT2"):
            stock = float(st[(st.date == d) & (st.ingredient_id == i) & (st.plant_id == plant)]["qty_kg"].sum()) if d is not None else 0.0
            c = pc[(pc.ingredient_id == i) & (pc.plant_id == plant)].groupby("week_start")["kg_consumed"].sum().sort_index().tail(4)
            use = float(c.sum() / (7 * len(c))) if len(c) else 0.0
            rw = stock / use if use > 0 else None
            blocked = i == "ING_PF" and route["status"] == "closed"
            status = "RED" if (rw is not None and rw < 24) or blocked and (rw or 0) < 35 else "AMBER" if (rw is not None and rw < 35) or blocked else "GREEN"
            ing.append({"ingredient": i, "name": stat[i]["name"], "plant": plant, "runway_days": rw, "stock_kg": stock, "status": status,
                        "blocked": blocked, "label": "Highway closed" if blocked else None})
    return {"asof": str(run.day.date()), "cells": g, "ingredients": ing,
            "rules": {"RED": f"earliest-likely (P10) stockout inside {run.params['risk_red_cover_days']} days, or run-rate cover under a week",
                      "AMBER": f"P10 under {run.params['risk_amber_cover_days']} days or expected stockout inside {run.params['risk_amber_cover_days'] + 7} days",
                      "GREEN": "covered", "EXCESS": f"cover above {run.params['overstock_cover_days']} days",
                      "INGREDIENT": "runway under 24 days (closure 21 + transit 3) is RED, under 35 AMBER"},
            "provenance": ["SYNTHETIC", "ESTIMATED"], "interval_type": "SCENARIO"}


def _risk_rank(status: str) -> int:
    return {"GREEN": 0, "AMBER": 1, "RED": 2}[status]


@serial
def sankey(svc: Service, day) -> dict:
    run = svc.run(day)
    ds = svc.ds
    asof = run.day
    win0 = asof - pd.Timedelta(days=28)
    g = pd.DataFrame(grid(svc, day))
    names = ds.depot_name()
    bom = ds.dims["bom"]
    sup = ds.dims["supplier"]
    route = PU.route_state(run)
    stat = {s["ingredient"]: s for s in PU.pulp_status(run, 150)}
    nodes: dict[str, dict] = {}
    links: list[dict] = []

    def node(id_: str, label: str, col: int, status: str = "GREEN", note: str = "", value: float | None = None) -> None:
        nodes[id_] = {"id": id_, "label": label, "col": col, "status": status, "note": note, "value": value}

    # production last 4 weeks by plant and flavor
    pr = run.snap.production
    pr = pr[(pr.week_start >= win0) & (pr.week_start <= asof)]
    prod = pr.groupby(["plant_id", "flavor_id"])["actual_bottles"].sum().reset_index()
    # supplier -> plant: each bottle splits over its ingredients by kg per bottle, then by supplier share
    flow_sp: dict[tuple[str, str], list] = {}
    for r in prod.itertuples():
        b = bom[bom.flavor_id == r.flavor_id]
        tot_kg = float(b["kg_per_bottle"].sum())
        for br in b.itertuples():
            for sr in sup[sup.ingredient_id == br.ingredient_id].itertuples():
                share = br.kg_per_bottle / tot_kg * sr.share_of_ingredient
                cur = flow_sp.setdefault((sr.supplier_id, r.plant_id), [0.0, 0.0])
                cur[0] += r.actual_bottles * share
                cur[1] += r.actual_bottles * br.kg_per_bottle * sr.share_of_ingredient
    for s in sup.itertuples():
        ing = stat.get(s.ingredient_id, {})
        status = "GREEN"
        note = f"{s.name}: {s.lead_time_days:g} day lead time"
        if s.supplier_id == "SUP06":
            if route["status"] == "closed":
                status, note = "RED", "Hill highway closed (landslide): the only road from the cooperative"
            elif ing.get("runway_days") is not None and ing["runway_days"] < 24:
                status, note = "AMBER", f"Passionfruit runway {ing['runway_days']:.0f} days, shorter than a 21-day closure plus transit"
        elif s.supplier_id == "SUP08" and ing.get("runway_days") is not None and ing["runway_days"] < 49:
            status, note = "AMBER", f"Imported dragonfruit takes {s.lead_time_days:g} days; runway {ing['runway_days']:.0f} days"
        node(s.supplier_id, s.name, 0, status, note)
    for pl in ("PLT1", "PLT2", "CPK1"):
        node(pl, {"PLT1": "Plant 1", "PLT2": "Plant 2", "CPK1": "Co-packer"}[pl], 1, "GREEN", "")
    for (sid, pid), (bt, kg) in flow_sp.items():
        links.append({"source": sid, "target": pid, "value": float(bt), "kg": float(kg), "unit": "bottle-equivalents",
                      "status": "RED" if (sid == "SUP06" and route["status"] == "closed") else "GREEN", "basis": "ESTIMATED: production x kg per bottle x supplier share"})
    nodes["CPK1"]["note"] = "No co-packer volume in the last 4 weeks (capacity unused)"
    # plant -> in transit -> depot
    it = run.snap.in_transit
    it = it[(it.movement == "plant") & (it.dispatch_date >= win0) & (it.dispatch_date <= asof)]
    node("TRANSIT", "In transit (trucks)", 2, "GREEN", "")
    flows_pd = it.groupby(["origin_id", "destination_id"])["qty_bottles"].sum().reset_index()
    for r in flows_pd.itertuples():
        links.append({"source": r.origin_id, "target": "TRANSIT", "value": float(r.qty_bottles), "unit": "bottles", "status": "GREEN", "basis": "OBSERVED shipments dispatched in the last 28 days"})
    inflight = it[it.eta >= asof]["qty_bottles"].sum()
    nodes["TRANSIT"]["note"] = f"{inflight:,.0f} bottles on the road at this date"
    depot_status = g.groupby("depot")["status"].agg(lambda s: max(s, key=_risk_rank)).to_dict()
    for r in ds.dims["depot"].itertuples():
        st = depot_status.get(r.depot_id, "GREEN")
        worst = g[(g.depot == r.depot_id)].sort_values("days_p10").head(1)
        note = f"{worst.iloc[0]['flavor']} earliest-likely stockout {worst.iloc[0]['date_p10']}" if len(worst) and st != "GREEN" else "Covered"
        node(r.depot_id, r.depot_name, 3, st, note)
    for r in it.groupby(["destination_id"])["qty_bottles"].sum().reset_index().itertuples():
        links.append({"source": "TRANSIT", "target": r.destination_id, "value": float(r.qty_bottles), "unit": "bottles", "status": "GREEN", "basis": "OBSERVED"})
    # depot -> channel (dispatch of the last 4 reported weeks)
    dp = run.snap.dispatch_weekly
    dp = dp[dp.week_start >= (win0 - pd.Timedelta(days=7))].sort_values("week_start")
    wks = sorted(dp["week_start"].unique())[-4:]
    dp = dp[dp.week_start.isin(wks)]
    # shelf-life filter on MT / QC: share of last-reported stock that is already GT-only
    st = run.snap.stock_weekly
    ld = st["stock_date"].max()
    lots = st[st.stock_date == ld].copy()
    lots["age"] = (asof - lots["production_date"]).dt.days
    gt_only = float(lots[lots["age"] > 30]["qty_bottles"].sum())
    tot = float(lots["qty_bottles"].sum())
    gt_share = gt_only / tot if tot > 0 else 0.0
    qy = run.snap.qc_daily
    qy = qy[qy.date == asof - pd.Timedelta(days=1)]
    qc_out = float((qy["stockout_flag"] == 1).mean()) if len(qy) else 0.0
    node("GT", "General trade", 4, "GREEN", "GT shelf-life rule unverified in the case (no cut applied)")
    node("MT", "Modern trade", 4, "AMBER" if gt_share > 0.10 else "GREEN",
         f"60-day filter: {gt_share * 100:.0f}% of stock at depots is already too old for MT/QC buyers (as last reported)")
    node("QC", "Quick commerce", 4, "RED" if qc_out > 0.4 else "AMBER" if (qc_out > 0.1 or gt_share > 0.10) else "GREEN",
         f"{qc_out * 100:.0f}% of depot-flavor QC lines flagged out yesterday; 60-day filter as MT")
    for r in dp.groupby(["depot_id", "channel_id"])["bottles_dispatched"].sum().reset_index().itertuples():
        links.append({"source": r.depot_id, "target": r.channel_id, "value": float(r.bottles_dispatched), "unit": "bottles", "status": "GREEN",
                      "basis": "OBSERVED dispatch, last 4 reported weeks"})
    return {"asof": str(asof.date()), "nodes": list(nodes.values()), "links": links, "window": "last 28 days (flows) / last 4 reported weeks (channels)",
            "note": "Line width is bottle volume. Supplier links are bottle-equivalents (kg converted by the recipe table). Only flows present in the data are drawn.",
            "provenance": ["SYNTHETIC", "OBSERVED", "ESTIMATED"]}


@serial
def rebalancing(svc: Service, day, max_lanes: int = 5) -> dict:
    """Up to five feasible depot-to-depot lanes, best first. A lane is feasible if the donor keeps its minimum cover and the
    bottles arrive before the recipient's earliest-likely stockout."""
    run = svc.run(day)
    ds = svc.ds
    key = ("lanes",)
    cache = run.__dict__.setdefault("_pcache", {})
    if key in cache:
        lanes = cache[key]
    else:
        g = [c for c in grid(svc, day) if c["status"] in ("RED", "AMBER")]
        g.sort(key=lambda c: c["days_p10"])
        cons = {"fg_used": {}, "donor_used": {}, "copacker_used": 0.0}
        pc: dict = {}
        cands = []
        for c in g[:16]:
            m = mitigation_options(run, c["flavor"], c["depot"], n=200, pos_cache=pc, constraints=cons)
            tr = next((o for o in m["options"] if o["key"] == "transfer"), None)
            if tr and tr["qty"] > 0:
                donor = tr["detail"]["donor"]
                cons["donor_used"][(c["flavor"], donor)] = cons["donor_used"].get((c["flavor"], donor), 0.0) + tr["qty"]
                cands.append({"flavor": c["flavor"], "from": donor, "to": c["depot"], "qty": tr["qty"], "arrival": tr["arrival_date"], "transit_days": 0,
                              "cost": tr["cost_total"], "on_time": not tr["arrives_too_late"], "net_value": tr["net_value_mean"],
                              "recipient_p10": m["stockout"]["date_p10"], "donor_surplus": tr["detail"].get("surplus")})
        lanes = [c for c in cands if c["on_time"]]
        lanes.sort(key=lambda c: -c["net_value"])
        cache[key] = lanes
    L = ds.dims["lane_depot"]
    for l in lanes:
        r = L[(L.from_depot == l["from"]) & (L.to_depot == l["to"])]
        l["transit_days"] = int(r.iloc[0]["transit_days"]) if len(r) else None
    dn = ds.depot_name()
    depots = [{"id": d, "name": dn[d], "x": CITY_XY[d][0], "y": CITY_XY[d][1], "region": ds.depot_region()[d]} for d in ds.depots]
    return {"asof": str(run.day.date()), "depots": depots, "lanes": lanes[:max_lanes], "found": len(lanes),
            "note": "Lanes shown are those that pass both checks today; fewer than five means fewer feasible moves exist, not a missing calculation.",
            "provenance": ["SYNTHETIC", "ESTIMATED"], "status": "PENDING decisions: see Decisions"}


@serial
def stock_check(svc: Service, day, flavor: str, depot: str, channel: str = "ALL") -> dict:
    run = svc.run(day)
    ds = svc.ds
    n = 300
    names, dn = ds.flavor_name(), ds.depot_name()
    depots = ds.depots if depot in ("ALL", None) else [depot]
    paths = None
    reported = 0.0
    recvs = 0.0
    rep_dates = set()
    for d in depots:
        pos = stock_position(run, flavor, d, n)
        paths = pos.nowcast_paths if paths is None else paths + pos.nowcast_paths
        reported += pos.reported_qty
        recvs += pos.receipts_since
        if pos.report_date is not None:
            rep_dates.add(str(pos.report_date.date()))
    q = np.quantile
    nowcast = {"p10": float(q(paths, .1)), "p50": float(q(paths, .5)), "p90": float(q(paths, .9))}
    out = {"flavor": flavor, "flavor_name": names[flavor], "depot": depot, "depot_name": "All depots" if depot in ("ALL", None) else dn[depot],
           "channel": channel, "reported": reported, "report_dates": sorted(rep_dates), "nowcast": nowcast, "receipts_since": recvs,
           "stock_note": "Depot stock is not split by channel in the case data (no channel priority). Channel view shows expected demand and eligibility.",
           "provenance": ["OBSERVED", "ESTIMATED"]}
    if depot not in ("ALL", None):
        so = stockout_distribution(run, flavor, depot, 90, n, pos=stock_position(run, flavor, depot, n))
        out["cover"] = so
        fc = get_forecast(run, flavor, channel=channel, depot=depot, horizon="7d") + get_forecast(run, flavor, channel=channel, depot=depot, horizon="14d") + \
            get_forecast(run, flavor, channel=channel, depot=depot, horizon="28d")
        out["forecast"] = fc
        ss = safety_stock(run, flavor, depot, n=500)
        out["safety"] = {k: ss[k] for k in ("target_final", "safety_stock", "old_rule", "gap_to_target", "service_level", "shelf_life_cap_applied")}
        reg = ds.depot_region()[depot]
        sp = spike_rows(run, flavor, region=reg)
        out["spike"] = sp[0] if sp else None
        cell = next((c for c in grid(svc, day) if c["flavor"] == flavor and c["depot"] == depot), None)
        out["status"] = cell
    else:
        fc = []
        for h in ("7d", "14d", "28d"):
            fc += get_forecast(run, flavor, channel=channel, horizon=h)
        out["forecast"] = fc
        cells = [c for c in grid(svc, day) if c["flavor"] == flavor]
        out["cells"] = cells
        out["status"] = {"status": max((c["status"] for c in cells), key=_risk_rank), "label": f"{sum(c['status'] == 'RED' for c in cells)} depots red"}
    return out


@serial
def weekly_review(svc: Service, day, economics: str = "margin") -> dict:
    run = svc.run(day)
    ds = svc.ds
    ct = control_tower(svc, day, economics)
    prev = svc.run(run.day - pd.Timedelta(days=7))
    g_now = grid(svc, day)
    g_prev = grid(svc, run.day - pd.Timedelta(days=7))
    red_now = sum(c["status"] == "RED" for c in g_now)
    red_prev = sum(c["status"] == "RED" for c in g_prev)
    aud = [a for a in ST.audit() if a["asof"] and pd.Timestamp(run.day - pd.Timedelta(days=7)) <= pd.Timestamp(a["asof"]) <= run.day and a["event"] != "CREATED"]
    return {"asof": str(run.day.date()), "week": f"{(run.day - pd.Timedelta(days=6)).date()} to {run.day.date()}", "headline": ct["headline"],
            "stock": {"red_now": red_now, "red_week_ago": red_prev, "excess": ct["stock"]["excess"]},
            "top_actions": ct["top_actions"], "pulp": ct["pulp"], "approvals": ct["approvals"],
            "decisions_this_week": aud, "qc": ct["qc"], "trust": svc.trust(day)["sources"],
            "provenance": ["SYNTHETIC", "ESTIMATED", "CALCULATED"], "interval_type": "SCENARIO"}
