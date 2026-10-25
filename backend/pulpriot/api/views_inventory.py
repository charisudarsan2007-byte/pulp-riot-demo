"""Inventory views: stock table (C1), nowcast tank (C2), location breakdown (C3), days of supply + stockout date (C4, C5),
safety stock (C6), sellable-life clock (C7), FEFO (C10), transfer preview (C12), regional shift (C15), root-cause domino (C16),
surge simulator (C17), information-delay clock (C18), speed-vs-cost (C21)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.loader import FLAVORS
from ..engine.forecast import get_forecast, spike_rows
from ..engine.hierarchy import build_paths
from ..engine.lost_demand import lost_demand
from ..inventory import mitigation as MT
from ..inventory import policy as PO
from ..inventory import position as PS
from ..inventory import risk as RK
from ..inventory import transfer as TR
from ..supply import pulp as PU
from .service import AWARENESS, Service
from .views_control import actions_state, grid


def stock_table(svc: Service, day) -> dict:
    run = svc.run(day)
    g = {(c["flavor"], c["depot"]): c for c in grid(svc, day)}
    names, dn = svc.ds.flavor_name(), svc.ds.depot_name()
    st = run.snap.stock_weekly
    ld = st["stock_date"].max()
    last = st[st.stock_date == ld].groupby(["depot_id", "flavor_id"])["qty_bottles"].sum()
    rows = []
    for fl in FLAVORS:
        for dp in svc.ds.depots:
            c = g[(fl, dp)]
            rows.append({"flavor": fl, "flavor_name": names[fl], "depot": dp, "depot_name": dn[dp], "reported": float(last.get((dp, fl), 0.0)),
                         "report_date": str(pd.Timestamp(ld).date()), "nowcast_p50": c["stock_p50"], "status": c["status"], "label": c["label"],
                         "excess": c["excess"], "cover_p10": c["days_p10"], "cover_p50": c["days_p50"], "date_p10": c["date_p10"], "date_p50": c["date_p50"],
                         "runrate_cover": c["runrate_cover_days"]})
    loc = locations(svc, day)
    return {"asof": str(run.day.date()), "report_date": str(pd.Timestamp(ld).date()), "rows": rows, "locations": loc,
            "provenance": ["OBSERVED", "ESTIMATED"], "note": "Reported = the depot's own weekly report. Nowcast = reported + receipts due - estimated outflow since the report."}


def locations(svc: Service, day) -> dict:
    """C1/C3: stock by location, not interchangeable: plant finished goods, trucks, depots, raw pulp (bottle-equivalents)."""
    run = svc.run(day)
    ds = svc.ds
    g = grid(svc, day)
    fg = run.snap.plant_fg
    fgl = fg[fg.week_start == fg.week_start.max()] if len(fg) else fg
    it = run.snap.in_transit
    inflight = it[(it.dispatch_date <= run.day) & (it.eta >= run.day)]
    bom = ds.dims["bom"]
    stat = {s["ingredient"]: s for s in PU.pulp_status(run, 100)}
    # raw pulp expressed as bottles the pulp could make, split between flavors that share an ingredient by kg per bottle
    pulp_bottles = {f: 0.0 for f in FLAVORS}
    for ing, s in stat.items():
        users = bom[bom.ingredient_id == ing]
        w = 1.0 / users["kg_per_bottle"]
        for r, wi in zip(users.itertuples(), w / w.sum()):
            pulp_bottles[r.flavor_id] += s["stock_kg"] * wi / r.kg_per_bottle
    # a flavor with several ingredients is limited by its scarcest: take the min across its own ingredients
    for fl in FLAVORS:
        b = bom[bom.flavor_id == fl]
        if len(b) > 1:
            pulp_bottles[fl] = min(stat[r.ingredient_id]["stock_kg"] / r.kg_per_bottle * (1.0 / len(bom[bom.ingredient_id == r.ingredient_id])) for r in b.itertuples())
    out = []
    for fl in FLAVORS:
        out.append({"flavor": fl, "name": ds.flavor_name()[fl],
                    "plant_fg": float(fgl[fgl.flavor_id == fl]["fg_bottles"].sum()) if len(fgl) else 0.0,
                    "in_transit": float(inflight[inflight.flavor_id == fl]["qty_bottles"].sum()),
                    "depot": float(sum(c["stock_p50"] for c in g if c["flavor"] == fl)),
                    "raw_pulp_bottle_eq": float(pulp_bottles[fl])})
    return {"rows": out, "note": "Raw pulp is shown as the bottles it could make (BOM kg per bottle; shared pulp split by recipe weight; multi-ingredient flavors limited by their scarcest). Locations cannot substitute for one another.",
            "plant_fg_week": None if fgl.empty else str(fgl["week_start"].max().date()), "provenance": ["OBSERVED", "ESTIMATED"]}


def tank(svc: Service, day, flavor: str, depot: str) -> dict:
    run = svc.run(day)
    pos = PS.stock_position(run, flavor, depot, 500)
    so = PS.stockout_distribution(run, flavor, depot, 90, 500, pos=pos)
    ss = PO.safety_stock(run, flavor, depot, n=800, pos=pos)
    traj = trajectory(run, flavor, depot, pos, 45, 500)
    lots = PO.lots_now(run, flavor, depot, pos)
    return {"asof": str(run.day.date()), "flavor": flavor, "depot": depot, "flavor_name": svc.ds.flavor_name()[flavor], "depot_name": svc.ds.depot_name()[depot],
            "report_date": None if pos.report_date is None else str(pos.report_date.date()),
            "report_received_at": None if pos.report_received_at is None else str(pos.report_received_at), "report_age_days": pos.gap_days,
            "reported": pos.reported_qty, "receipts_since": pos.receipts_since, "receipts_detail": pos.receipts_detail,
            "outflow_since": {"qc_observed": pos.qc_observed_since, "gtmt_estimated_p50": pos.gtmt_est_since.get("p50", 0.0),
                              "gtmt_estimated_p10": pos.gtmt_est_since.get("p10", 0.0), "gtmt_estimated_p90": pos.gtmt_est_since.get("p90", 0.0)},
            "nowcast": pos.nowcast, "cover": so, "safety": ss, "trajectory": traj,
            "inbound": [{"shipment": r.shipment_id, "qty": float(r.qty_bottles), "eta": str(pd.Timestamp(r.eta).date()), "from": r.origin_id} for r in pos.inbound.itertuples()],
            "equation": "nowcast = reported stock + receipts since report - (observed QC sales + estimated GT/MT outflow since report), floored at zero",
            "lots_total": float(lots["qty_now"].sum()) if len(lots) else 0.0,
            "provenance": ["OBSERVED", "ESTIMATED", "PROXY"], "interval_type": so["interval_type"]}


def trajectory(run, flavor: str, depot: str, pos: PS.Position, horizon: int, n: int) -> list[dict]:
    """Day-by-day stock quantiles with confirmed inbound and no further replenishment."""
    ps = build_paths(run, flavor, depot, days=horizon, n=len(pos.nowcast_paths))
    D = ps.total
    arr = MT.arrivals_vector(run, pos, horizon)
    stock = pos.nowcast_paths.copy()
    out = []
    for t in range(horizon):
        avail = stock + arr[t]
        sold = np.minimum(D[:, t], avail)
        stock = avail - sold
        out.append({"date": str((run.day + pd.Timedelta(days=t)).date()), "day": t, "p10": float(np.quantile(stock, .1)), "p50": float(np.quantile(stock, .5)),
                    "p90": float(np.quantile(stock, .9))})
    return out


def cover_view(svc: Service, day, flavor: str, depot: str, surge: float = 1.0, rho: float | None = None) -> dict:
    run = svc.with_rho(day, rho)
    pos = PS.stock_position(run, flavor, depot, 500)
    normal = PS.stockout_distribution(run, flavor, depot, 90, 500, pos=pos)
    high = PS.stockout_distribution(run, flavor, depot, 90, 500, pos=pos, surge=max(surge, 1.0))
    rr = PS.recent_runrate_cover(run, flavor, depot, pos)
    return {"asof": str(run.day.date()), "flavor": flavor, "depot": depot, "normal": normal, "high": high, "surge": max(surge, 1.0), "runrate": rr,
            "definition": normal["definition"], "provenance": ["ESTIMATED", "CALCULATED"], "interval_type": normal["interval_type"]}


def safety_view(svc: Service, day, flavor: str, depot: str, service_level: float | None = None) -> dict:
    run = svc.run(day)
    ss = PO.safety_stock(run, flavor, depot, service_level, n=1000)
    return {"asof": str(run.day.date()), **ss, "provenance": ["ESTIMATED", "ASSUMED"]}


def sellable_view(svc: Service, day, flavor: str, depot: str) -> dict:
    run = svc.run(day)
    pos = PS.stock_position(run, flavor, depot, 300)
    lots = PO.lots_now(run, flavor, depot, pos)
    return {"asof": str(run.day.date()), "flavor": flavor, "depot": depot, **PO.sellable_clock(run, flavor, depot, pos, lots),
            "fefo": PO.fefo_plan(run, flavor, depot, pos, lots)}


def transfer_view(svc: Service, day, flavor: str, donor: str, recipient: str, qty: float) -> dict:
    run = svc.run(day)
    r = TR.transfer_preview(run, flavor, donor, recipient, qty, n=500)
    return {"asof": str(run.day.date()), **r}


def shift_view(svc: Service, day) -> dict:
    run = svc.run(day)
    rows = RK.demand_shift(run)
    names, dn = svc.ds.flavor_name(), svc.ds.depot_name()
    for r in rows:
        r["flavor_name"], r["depot_name"] = names[r["flavor"]], dn[r["depot"]]
    return {"asof": str(run.day.date()), "rows": rows, "threshold": {"points": 3, "relative": 0.2}, "provenance": ["SYNTHETIC", "CENSOR_ADJUSTED", "CALCULATED"]}


def surge_view(svc: Service, day, flavor: str, multiplier: float) -> dict:
    run = svc.run(day)
    rows = RK.surge_table(run, flavor, multiplier, n=300)
    dn = svc.ds.depot_name()
    for r in rows:
        r["depot_name"] = dn[r["depot"]]
    return {"asof": str(run.day.date()), "flavor": flavor, "multiplier": multiplier, "rows": rows,
            "note": "Demand is multiplied from today for every channel. Stock is the nowcast plus confirmed inbound; no further replenishment.", "provenance": ["ESTIMATED"]}


def delay_clock(svc: Service, day, flavor: str | None = None, economics: str = "margin") -> dict:
    """C18: days between the first detectable surge and planner awareness, and the cost of each day. Costs are accrued lost
    value from the censored-days estimate, not forward exposure, so they never double-count the waterfall."""
    run = svc.run(day)
    names = svc.ds.flavor_name()
    rows = [r for r in spike_rows(run, flavor, region="ALL") if r["stage"] not in ("NORMAL", "REVERTED") or (r["first_dates"].get("WATCH"))]
    if not rows:
        return {"asof": str(run.day.date()), "active": False, "text": "No demand surge has been detected yet.", "provenance": ["CALCULATED"]}
    r = sorted(rows, key=lambda x: -abs(np.log(max(x["ratio_R"], 1e-3))))[0]
    fl = r["flavor"]
    watch = r["first_dates"].get("WATCH")
    start = pd.Timestamp(watch) if watch else run.day
    aware = AWARENESS if fl == "DP" and run.day >= AWARENESS else None
    end = aware if aware is not None else run.day
    elapsed = int((end - start).days)
    lost = lost_demand(run, fl, since=start, n=500)
    allr = next(x for x in lost if x["channel"] == "ALL")
    mult = run.params["margin"] if economics == "margin" else 1.0
    val = allr["lost_p50"] * run.params["price"] * mult
    per_day = val / max(elapsed, 1)
    return {"asof": str(run.day.date()), "active": True, "flavor": fl, "name": names[fl], "signal_date": str(start.date()), "awareness_date": None if aware is None else str(aware.date()),
            "awareness_note": "As-was planner awareness (replay label from the workbook) is 2026-04-06; before that date the clock is still running." if fl == "DP" else "",
            "days_elapsed": elapsed, "lost_bottles_p50": allr["lost_p50"], "lost_bottles_p10": allr["lost_p10"], "lost_bottles_p90": allr["lost_p90"],
            "accrued_value_p50": val, "cost_per_day": per_day, "economics": economics,
            "basis": "Lost bottles (P50 of summed paths on censored days, floored at zero) x " + ("price x 32% margin" if economics == "margin" else "price") + " / days elapsed",
            "provenance": ["ESTIMATED", "CENSOR_ADJUSTED"], "interval_type": "SCENARIO", "caveat": "Lower bound: observed sales stop rising once shelves empty, so the engine sees only the first part of the surge."}


def mitigation_view(svc: Service, day, flavor: str, depot: str, economics: str = "margin") -> dict:
    run = svc.run(day)
    m = MT.mitigation_options(run, flavor, depot, n=500, economics=economics)
    return {"asof": str(run.day.date()), **m}


def domino(svc: Service, day, flavor: str, depot: str) -> dict:
    """C16: the chain from the first signal to a decision, each link tagged as observed fact, model inference or possible outcome."""
    run = svc.run(day)
    names, dn = svc.ds.flavor_name(), svc.ds.depot_name()
    region = svc.ds.depot_region()[depot]
    sp = spike_rows(run, flavor, region=region) + spike_rows(run, flavor, region="ALL")
    reg = sp[0] if sp else None
    nat = sp[-1] if sp else None
    pos = PS.stock_position(run, flavor, depot, 400)
    with_event = PS.stockout_distribution(run, flavor, depot, 90, 400, pos=pos)
    base_ps = build_paths(run, flavor, depot, days=90, n=400, no_event=True)
    D = base_ps.total
    arr = MT.arrivals_vector(run, pos, 90)
    cum_s = pos.nowcast_paths[:, None] + np.cumsum(arr)[None, :]
    first = np.where((np.cumsum(D, axis=1) > cum_s).any(axis=1), (np.cumsum(D, axis=1) > cum_s).argmax(axis=1), 90)
    base_p50, base_p10 = float(np.quantile(first, .5)), float(np.quantile(first, .1))
    lost = lost_demand(run, flavor, n=300)
    lall = next(x for x in lost if x["channel"] == "ALL")
    other = [c for c in grid(svc, day) if c["flavor"] == flavor and c["depot"] != depot]
    surplus = sorted([c for c in other if (c["simple_cover_days"] or 0) > 30], key=lambda c: -(c["simple_cover_days"] or 0))[:2]
    acts = [a for a in actions_state(svc, day) if a["flavor"] == flavor and a["depot"] == depot and a["status_at_asof"] == "PENDING" and a["active"]]
    chain = []
    if reg and reg["stage"] not in ("NORMAL", "REVERTED"):
        chain.append({"step": 1, "kind": "OBSERVED", "title": "Early signal",
                      "text": f"Quick-commerce sales of {names[flavor]} in {region} reached {reg['ratio_R']:.1f}x baseline; first watch flag {reg['first_dates'].get('WATCH') or 'n/a'}, alert {reg['first_dates'].get('ALERT') or 'n/a'}.",
                      "tag": "OBSERVED (units sold)"})
        ex = (nat.get("extra_bottles") or {}).get("28") if nat else None
        chain.append({"step": 2, "kind": "MODEL", "title": "Forecast changes",
                      "text": (f"Demand over the next 28 days is raised by about {ex['p50']:,.0f} bottles nationally (P10 {ex['p10']:,.0f}, P90 {ex['p90']:,.0f})." if ex else "No event size available."),
                      "tag": "ESTIMATED (EVENT_SCENARIO)"})
    else:
        chain.append({"step": 1, "kind": "OBSERVED", "title": "Early signal", "text": f"No live demand event for {names[flavor]} in {region}; demand is near baseline.", "tag": "OBSERVED"})
    chain.append({"step": len(chain) + 1, "kind": "MODEL", "title": "Cover drops",
                  "text": f"At {dn[depot]} cover falls from about {base_p50:.0f} days (baseline demand) to {with_event['days_p50']:.0f} days with the event.", "tag": "ESTIMATED"})
    chain.append({"step": len(chain) + 1, "kind": "MODEL", "title": "Stockout risk",
                  "text": f"Earliest-likely stockout {with_event['date_p10']}, expected {with_event['date_p50']}.", "tag": "ESTIMATED (SCENARIO)"})
    chain.append({"step": len(chain) + 1, "kind": "INFERENCE", "title": "Stockouts hide true demand",
                  "text": f"Once shelves empty, sales stop rising. The engine treats those days as lower bounds and estimates {lall['lost_p50']:,.0f} bottles of {names[flavor]} demand so far went unserved (P10 {lall['lost_p10']:,.0f}, P90 {lall['lost_p90']:,.0f}).",
                  "tag": "ESTIMATED (CENSOR_ADJUSTED)"})
    if surplus:
        chain.append({"step": len(chain) + 1, "kind": "OBSERVED", "title": "Stock sits elsewhere",
                      "text": "Meanwhile " + ", ".join(f"{dn[c['depot']]} holds about {c['simple_cover_days']:.0f} days of cover" for c in surplus) + f" of the same flavor, the pattern behind last summer's write-offs.", "tag": "ESTIMATED"})
    chain.append({"step": len(chain) + 1, "kind": "DECISION", "title": "Human decision",
                  "text": (f"{len(acts)} recommendation(s) waiting: " + "; ".join(a["title"] for a in acts[:2])) if acts else "No recommendation is waiting for this depot and flavor.",
                  "tag": "PENDING approval"})
    return {"asof": str(run.day.date()), "flavor": flavor, "depot": depot, "chain": chain,
            "legend": {"OBSERVED": "A reported fact", "MODEL": "Model estimate", "INFERENCE": "Inferred from censored data", "DECISION": "Waiting for a named person"},
            "provenance": ["OBSERVED", "ESTIMATED"]}
