"""Ask the Agent (A1): plain questions are routed to validated engine and database functions.

There is no language model in this path. The router recognises a flavor, a depot, a channel and an intent from the supplied synonym
table, calls the same functions the screens use, and writes the answer from the computed values with their source tag and freshness.
If the question is not understood it says so and lists what it can answer; it never guesses a number."""
from __future__ import annotations

import difflib
import re

import numpy as np
import pandas as pd

from ..data.loader import Dataset

INTENTS = [
    ("approvals", r"approv|waiting for|decision|pending|sign.?off"),
    ("copacker", r"co.?packer|extra production|outsourc"),
    ("pulp", r"pulp|passionfruit supply|dragonfruit supply|runway|ingredient|highway|import"),
    ("lost", r"lost|lose|missed sales|stockout cost|cost of"),
    ("shelf", r"shelf|expir|60.?day|sellable|fefo|oldest|stale stock|too old"),
    ("runout", r"run out|runout|stock.?out|when will|how long will|last|days of (supply|cover)|cover"),
    ("risk", r"risk|danger|short(age)?|which (depots?|warehouses?|flavou?rs?)|worst|red\b|problem"),
    ("stock", r"stock|inventory|how (much|many)|bottles|have we got|on hand"),
    ("spike", r"spike|surge|selling|demand|running at|viral|fast|slow|trend"),
    ("forecast", r"forecast|next (week|month)|expect|predict|will sell"),
    ("trust", r"fresh|stale|trust|source|how old|data quality|reliable|up to date"),
]


def _terms(ds: Dataset):
    syn = ds.dims["synonym"]
    f = {str(r["term"]).lower(): r["entity_id"] for _, r in syn.iterrows() if r["entity_type"] == "flavor"}
    d = {str(r["term"]).lower(): r["entity_id"] for _, r in syn.iterrows() if r["entity_type"] == "depot"}
    c = {str(r["term"]).lower(): r["entity_id"] for _, r in syn.iterrows() if r["entity_type"] == "channel"}
    for _, r in ds.dims["flavor"].iterrows():
        f[r["flavor_name"].lower()] = r["flavor_id"]
    for _, r in ds.dims["depot"].iterrows():
        d[r["depot_name"].lower()] = r["depot_id"]
    return f, d, c


def _find(text: str, terms: dict[str, str]) -> str | None:
    best = None
    for t in sorted(terms, key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", text):
            best = terms[t]
            break
    if best:
        return best
    for w in re.findall(r"[a-z]{4,}", text):
        m = difflib.get_close_matches(w, list(terms), n=1, cutoff=0.84)
        if m:
            return terms[m[0]]
    return None


def parse(ds: Dataset, q: str) -> dict:
    text = q.lower()
    f, d, c = _terms(ds)
    flavor, depot, channel = _find(text, f), _find(text, d), _find(text, c)
    intent = next((name for name, pat in INTENTS if re.search(pat, text)), None)
    return {"intent": intent, "flavor": flavor, "depot": depot, "channel": channel}


def answer(svc, day, question: str) -> dict:
    from ..api import views_control as VC
    from ..api import views_demand as VD
    from ..api import views_inventory as VI
    from ..api import views_replay as VR
    from ..engine.forecast import get_forecast
    from ..supply import capacity as CA
    from ..supply import pulp as PU
    ds = svc.ds
    run = svc.run(day)
    p = parse(ds, question)
    names, dn = ds.flavor_name(), ds.depot_name()
    asof = str(run.day.date())
    fresh = f"as of {asof}, data arrived up to {run.ts}"
    src: list[dict] = []
    rows: list[dict] = []
    follow: list[str] = []
    text = ""
    intent = p["intent"]
    fl, dp, ch = p["flavor"], p["depot"], p["channel"] or "ALL"

    def need(what: str, options: list[str]) -> dict:
        return {"answer": f"Which {what}? I can answer for: {', '.join(options)}.", "understood": p, "clarify": what, "sources": [], "rows": [], "freshness": fresh,
                "method": "rule-based router; no generated numbers", "followups": []}

    if intent is None:
        return {"answer": "I can answer questions about stock, days of cover and stockout dates, spikes in sales, forecasts, pulp runway, the co-packer deadline, shelf life, lost sales, "
                          "data freshness and what is waiting for approval. Try: \"When will Dragon Passion run out in Mumbai?\" or \"What needs approval?\"",
                "understood": p, "sources": [], "rows": [], "freshness": fresh, "method": "rule-based router; no generated numbers", "followups": [
                    "Which depots are at risk?", "How is Dragon Passion selling?", "How many days of passionfruit pulp are left?", "What needs approval?"]}

    if intent in ("stock", "runout") and fl and dp:
        sc = VC.stock_check(svc, day, fl, dp, ch)
        c = sc["cover"]
        if intent == "stock":
            text = (f"{names[fl]} at {dn[dp]}: about {sc['nowcast']['p50']:,.0f} bottles today (range {sc['nowcast']['p10']:,.0f} to {sc['nowcast']['p90']:,.0f}). "
                    f"The last depot report ({', '.join(sc['report_dates'])}) said {sc['reported']:,.0f}; this is an estimate between weekly reports.")
        else:
            text = (f"{names[fl]} at {dn[dp]} is expected to run out around {c['date_p50']} ({c['days_p50']:.0f} days); the earliest-likely date is {c['date_p10']} "
                    f"({c['days_p10']:.0f} days). No further replenishment is assumed beyond shipments already on the road.")
        src = [{"function": "stock_position / stockout_distribution", "tag": "ESTIMATED, SCENARIO interval", "freshness": f"depot report {', '.join(sc['report_dates'])}"}]
        rows = [{"metric": "Reported stock", "value": sc["reported"]}, {"metric": "Nowcast P50", "value": sc["nowcast"]["p50"]}, {"metric": "Stockout P10", "value": c["date_p10"]},
                {"metric": "Stockout P50", "value": c["date_p50"]}]
        follow = [f"What is the safety stock for {names[fl]} at {dn[dp]}?", f"How is {names[fl]} selling?"]
    elif intent in ("stock", "runout") and fl and not dp:
        sc = VC.stock_check(svc, day, fl, "ALL", ch)
        reds = [c for c in sc["cells"] if c["status"] == "RED"]
        text = (f"{names[fl]} across all depots: about {sc['nowcast']['p50']:,.0f} bottles today (range {sc['nowcast']['p10']:,.0f} to {sc['nowcast']['p90']:,.0f}). "
                f"{len(reds)} of 9 depots are red" + (": " + ", ".join(dn[c["depot"]] for c in reds[:5]) + "." if reds else "."))
        src = [{"function": "stock_position (summed by path)", "tag": "ESTIMATED", "freshness": fresh}]
        rows = [{"depot": dn[c["depot"]], "status": c["status"], "stockout_P10": c["date_p10"], "stockout_P50": c["date_p50"]} for c in sc["cells"]]
    elif intent in ("stock", "runout"):
        return need("flavor", [r["name"] for r in svc.meta()["flavors"]])
    elif intent == "risk":
        g = [c for c in VC.grid(svc, day) if c["status"] == "RED" and (not fl or c["flavor"] == fl) and (not dp or c["depot"] == dp)]
        g.sort(key=lambda c: c["days_p10"])
        text = (f"{len(g)} depot-flavor lines are red" + (f" for {names[fl]}" if fl else "") + (f" at {dn[dp]}" if dp else "") + ". " +
                ("Earliest-likely stockouts: " + "; ".join(f"{names[c['flavor']]} at {dn[c['depot']]} on {c['date_p10']}" for c in g[:4]) + "." if g else "Nothing is red right now."))
        src = [{"function": "risk_grid", "tag": "ESTIMATED, SCENARIO interval", "freshness": fresh}]
        rows = [{"flavor": names[c["flavor"]], "depot": dn[c["depot"]], "stockout_P10": c["date_p10"], "stockout_P50": c["date_p50"]} for c in g[:12]]
        follow = ["What needs approval?"]
    elif intent == "spike":
        r = VD.explainer(svc, day, fl)
        text = r["line"]["text"]
        src = [{"function": "get_spike_status", "tag": "ESTIMATED, EVENT_SCENARIO (UNITS_SOLD_SIGNAL)", "freshness": "previous day"}]
        follow = [f"What is the forecast for {r['name']}?"]
    elif intent == "forecast":
        flv = [fl] if fl else ["DP", "MPB", "GC", "PP", "DB"]
        for f_ in flv:
            for h in ("7d", "28d"):
                x = get_forecast(run, f_, ch, None, dp, h)[0]
                rows.append({"flavor": names[f_], "horizon": h, "P10": round(x["p10"]), "P50": round(x["p50"]), "P90": round(x["p90"]), "interval": x["interval_type"]})
        text = "Forecast bottles" + (f" for {names[fl]}" if fl else "") + (f" at {dn[dp]}" if dp else " nationally") + ": " + \
               "; ".join(f"{r['flavor']} {r['horizon']}: {r['P50']:,} (P10 {r['P10']:,}, P90 {r['P90']:,})" for r in rows[:6]) + ". Ranges are scenario intervals (uncalibrated)."
        src = [{"function": "get_forecast", "tag": "ESTIMATED, SCENARIO/EVENT_SCENARIO", "freshness": fresh}]
    elif intent == "pulp":
        st = PU.pulp_status(run, 150)
        pf = next(s for s in st if s["ingredient"] == "ING_PF")
        df_ = next(s for s in st if s["ingredient"] == "ING_DF")
        text = (f"Passionfruit pulp: {pf['stock_kg']:,.0f} kg, about {pf['runway_days']:.0f} days at the last four weeks' use. Dragonfruit pulp: {df_['stock_kg']:,.0f} kg, about "
                f"{df_['runway_days']:.0f} days. The hill highway is {pf['route']['status'] if pf['route'] else 'open'}.")
        src = [{"function": "pulp_status", "tag": "CALCULATED from plant pulp snapshot", "freshness": pf["snapshot_date"]}]
        rows = [{"ingredient": s["name"], "stock_kg": round(s["stock_kg"]), "runway_days": round(s["runway_days"] or 0, 1)} for s in st if s["critical"]]
    elif intent == "approvals":
        acts = VC.actions_state(svc, day)
        pend = [a for a in acts if a["status_at_asof"] == "PENDING" and a["active"]]
        top = VC.top_actions(svc, day, "margin", 5)
        text = f"{len(pend)} recommendations are waiting for a decision. Largest first: " + "; ".join(f"{a['title']} (approver {a['approver_role'].replace('ROLE_', '')}, by {a['deadline']})" for a in top[:3]) + "."
        src = [{"function": "action register", "tag": "PENDING until approved", "freshness": fresh}]
        rows = [{"action": a["title"], "approver": a["approver_role"], "deadline": a["deadline"]} for a in top]
    elif intent == "copacker":
        r = CA.copacker_deadline(run, VC.grid(svc, day))
        h = r["headline"]
        text = (f"The most urgent co-packer order is {names[h['flavor']]} for {dn[h['depot']]}: order by {h['order_by']} ({h['days_left']} days from today) to land before the earliest-likely stockout on {h['p10_stockout']}."
                if h else "No depot-flavor line needs a co-packer order right now.") + f" Co-packer notice is {r['notice_days']} days."
        src = [{"function": "copacker_deadline", "tag": "ESTIMATED, lane ASSUMED", "freshness": fresh}]
        rows = r["rows"][:6]
    elif intent == "lost":
        from ..engine.lost_demand import lost_demand
        lo = [x for x in lost_demand(run, fl, n=400) if x["channel"] == "ALL"]
        tot = sum(x["lost_p50"] for x in lo)
        text = (f"Estimated unserved demand so far{(' for ' + names[fl]) if fl else ''}: about {tot:,.0f} bottles (about Rs {tot * ds.params['price'] / 1e7:.1f} crore of revenue), "
                f"from days when shelves were empty. This is a lower bound: capped sales hide the full size of a surge.")
        src = [{"function": "get_lost_demand", "tag": "ESTIMATED, CENSOR_ADJUSTED", "freshness": fresh}]
        rows = [{"flavor": names[x["flavor"]], "P10": round(x["lost_p10"]), "P50": round(x["lost_p50"]), "P90": round(x["lost_p90"])} for x in lo]
    elif intent == "shelf":
        if fl and dp:
            sv = VI.sellable_view(svc, day, fl, dp)
            text = (f"{names[fl]} at {dn[dp]}: the oldest lot still sellable to MT/QC has {sv['oldest_eligible_days_left']} days left before buyers reject it (60 of 90 days rule). "
                    f"About {sv['at_risk_mtqc']:,.0f} bottles will not sell to MT/QC in time at the current rate.")
            rows = sv["buckets"]
        else:
            acts = [a for a in VC.actions_state(svc, day) if a["type"] == "fefo_redirect" and a["active"] and a["status_at_asof"] == "PENDING"]
            text = f"{len(acts)} depot-flavor lines hold stock that will miss the MT/QC cutoff. " + ("; ".join(a["title"] for a in acts[:3]) if acts else "")
            rows = [{"action": a["title"]} for a in acts[:6]]
        src = [{"function": "sellable_clock / FEFO", "tag": "OBSERVED lots, ESTIMATED sell-through; GT rule unverified", "freshness": fresh}]
    elif intent == "trust":
        tr = svc.trust(day)
        stale = [s for s in tr["sources"] if s["stale"]]
        text = ("All sources are inside their freshness limits." if not stale else "Stale: " + ", ".join(s["label"] for s in stale) + ".") + \
               f" The quick-commerce feed has no orders column, so detection uses units sold."
        src = [{"function": "trust summary", "tag": "CALCULATED", "freshness": fresh}]
        rows = [{"source": s["label"], "age_days": s["age_days"], "limit": s["limit_days"], "stale": s["stale"]} for s in tr["sources"]]
    else:
        return need("flavor and depot", [r["name"] for r in svc.meta()["flavors"]])

    return {"answer": text, "understood": p, "sources": src, "rows": rows, "freshness": fresh, "asof": asof, "method": "rule-based router over engine functions; no generated numbers",
            "followups": follow}
