"""Supply: pulp runway (E1), ingredient conflict (E2), pulp expiry (E13), scarce-pulp trade-off (E15), inbound corridor (E17).

All pulp stock figures come from the plant cold-store snapshot (T14) that had arrived by the as-of moment. Purchase-order
ETAs use the PLANNED date only: realised ETAs and delays in the workbook are hindsight and are dropped at load."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..engine.hierarchy import region_total_paths
from ..engine.pipeline import EngineRun

CRITICAL_INGREDIENTS = ("ING_PF", "ING_DF")


def bom(run: EngineRun) -> pd.DataFrame:
    return run.ds.dims["bom"]


def ingredient_names(run: EngineRun) -> dict[str, str]:
    d = run.ds.dims["ingredient"]
    return dict(zip(d["ingredient_id"], d["name"]))


def route_state(run: EngineRun, supplier: str = "SUP06") -> dict:
    rs = run.snap.route_status
    rs = rs[rs.supplier_id == supplier]
    if rs.empty:
        return {"status": "open", "closure_day": 0, "known_at": None}
    last = rs.sort_values("date").iloc[-1]
    return {"status": last["status"], "closure_day": int(last["closure_day_n"]), "known_at": str(last["known_at"]), "date": str(last["date"].date())}


def stock_snapshot(run: EngineRun) -> tuple[pd.Timestamp | None, pd.DataFrame]:
    ps = run.snap.pulp_stock
    if ps.empty:
        return None, ps
    d = ps["date"].max()
    return d, ps[ps.date == d]


def daily_use(run: EngineRun, ingredient: str, weeks: int = 4) -> float:
    """kg per day over the last `weeks` complete reported weeks (T15, DERIVED from production)."""
    pc = run.snap.pulp_consumption
    pc = pc[pc.ingredient_id == ingredient]
    if pc.empty:
        return 0.0
    wk = pc.groupby("week_start")["kg_consumed"].sum().sort_index().tail(weeks)
    return float(wk.sum() / (7.0 * len(wk)))


def inbound_pos(run: EngineRun, ingredient: str) -> pd.DataFrame:
    po = run.snap.purchase_orders
    po = po[(po.ingredient_id == ingredient) & (po.planned_eta >= run.day)]
    return po[["po_id", "supplier_id", "qty_kg", "order_date", "planned_eta", "mode"]].copy()


def forecast_use(run: EngineRun, ingredient: str, days: int = 28, n: int = 300) -> np.ndarray:
    """Path-wise kg/day needed to make the forecast bottle demand (upper bound: latent demand, plants cannot add lines)."""
    b = bom(run)
    b = b[b.ingredient_id == ingredient]
    tot = np.zeros(n)
    for r in b.itertuples():
        arr, _ = region_total_paths(run, r.flavor_id, "ALL", days, n)
        tot += arr[:, :days].sum(axis=1) * r.kg_per_bottle
    return tot / days


def pulp_status(run: EngineRun, n: int = 300) -> list[dict]:
    cache = run.__dict__.setdefault("_pcache", {})
    key = ("pulp_status",)
    if key not in cache:
        cache[key] = _pulp_status(run, 300)
    return cache[key]


def _pulp_status(run: EngineRun, n: int = 300) -> list[dict]:
    d, snap = stock_snapshot(run)
    names = ingredient_names(run)
    out = []
    route = route_state(run)
    for ing in names:
        s = snap[snap.ingredient_id == ing] if d is not None else snap
        stock = float(s["qty_kg"].sum()) if len(s) else 0.0
        by_plant = s.groupby("plant_id")["qty_kg"].sum().to_dict() if len(s) else {}
        use = daily_use(run, ing)
        runway = stock / use if use > 0 else None
        fu = forecast_use(run, ing, 28, n) if ing in CRITICAL_INGREDIENTS else None
        fr = None
        if fu is not None:
            rw = stock / np.maximum(fu, 1e-9)
            fr = {"p10": float(np.quantile(rw, .1)), "p50": float(np.quantile(rw, .5)), "p90": float(np.quantile(rw, .9)), "use_p50": float(np.median(fu))}
        inb = inbound_pos(run, ing)
        blocked = bool(len(inb) and route["status"] == "closed" and (inb["supplier_id"] == "SUP06").any())
        out.append({"ingredient": ing, "name": names[ing], "stock_kg": stock, "by_plant": by_plant, "daily_use_kg": use,
                    "runway_days": runway, "forecast_runway": fr, "snapshot_date": None if d is None else str(d.date()),
                    "inbound_kg": float(inb["qty_kg"].sum()) if len(inb) else 0.0,
                    "inbound_blocked_by_closure": blocked, "critical": ing in CRITICAL_INGREDIENTS,
                    "route": route if ing == "ING_PF" else None,
                    "provenance": ["SYNTHETIC", "CALCULATED"]})
    return out


def closure_exposure(run: EngineRun, closure_days: int, transit_days: int = 3) -> dict:
    """E1/E17: if the hill highway shut for `closure_days`, how much passionfruit pulp is short and what does an air bridge cost?"""
    st = {s["ingredient"]: s for s in pulp_status(run, 200)}
    pf = st["ING_PF"]
    use = pf["daily_use_kg"]
    need_days = closure_days + transit_days
    short_days = max(need_days - (pf["runway_days"] or 0.0), 0.0)
    kg = short_days * use
    air = kg * run.params["air_freight_inr_per_kg"]
    normal = kg * run.params["air_freight_inr_per_kg"] / run.params["air_freight_multiple"]
    return {"closure_days": closure_days, "transit_days": transit_days, "runway_days": pf["runway_days"], "days_short": short_days,
            "kg_short": kg, "air_cost_inr": air, "normal_cost_inr": normal, "air_rate": run.params["air_freight_inr_per_kg"],
            "tier": "CRITICAL" if short_days > 0 else ("WATCH" if (pf["runway_days"] or 0) < need_days + 14 else "OK"),
            "rule": "CRITICAL when pulp runway is shorter than the remaining closure plus 3-day transit.",
            "provenance": ["SYNTHETIC", "CALCULATED"]}


# ---------------------------------------------------------------------------------------------------------
def conflict(run: EngineRun, rule: str = "pro_rata", days: int = 28, n: int = 300) -> dict:
    """E2: when a shared pulp runs short, which drinks lose volume. Rules: pro_rata, dp_first (as-was), margin_per_kg."""
    d, snap = stock_snapshot(run)
    b = bom(run)
    names = run.ds.flavor_name()
    results = []
    route = route_state(run)
    for ing in CRITICAL_INGREDIENTS:
        stock = float(snap[snap.ingredient_id == ing]["qty_kg"].sum()) if d is not None else 0.0
        inbound = inbound_pos(run, ing)
        horizon_end = run.day + pd.Timedelta(days=days)
        inb = inbound[inbound.planned_eta <= horizon_end]
        if route["status"] == "closed":
            inb = inb[inb.supplier_id != "SUP06"]
        avail = stock + float(inb["qty_kg"].sum())
        users = b[b.ingredient_id == ing]
        need = {}
        for r in users.itertuples():
            arr, _ = region_total_paths(run, r.flavor_id, "ALL", days, n)
            need[r.flavor_id] = (float(np.median(arr[:, :days].sum(axis=1))) * r.kg_per_bottle, r.kg_per_bottle, float(np.median(arr[:, :days].sum(axis=1))))
        total_need = sum(v[0] for v in need.values())
        alloc = {}
        if total_need <= avail:
            alloc = {f: v[0] for f, v in need.items()}
        elif rule == "dp_first":
            left = avail
            for f in sorted(need, key=lambda x: 0 if x == "DP" else 1):
                give = min(need[f][0], left)
                alloc[f] = give
                left -= give
        elif rule == "margin_per_kg":
            left = avail
            for f in sorted(need, key=lambda x: -1.0 / need[x][1]):          # fewest kg per bottle = most bottles per kg
                give = min(need[f][0], left)
                alloc[f] = give
                left -= give
        else:
            share = avail / total_need
            alloc = {f: v[0] * share for f, v in need.items()}
        for f, v in need.items():
            made = alloc[f] / v[1]
            loss = max(v[2] - made, 0.0)
            pct = loss / v[2] if v[2] > 0 else 0.0
            results.append({"ingredient": ing, "flavor": f, "flavor_name": names[f], "demand_bottles": v[2], "pulp_needed_kg": v[0],
                            "pulp_allocated_kg": alloc[f], "bottles_possible": made, "bottles_lost": loss, "loss_share": pct,
                            "status": "RED" if pct > 0.10 else "AMBER" if pct > 0 else "GREEN",
                            "pulp_available_kg": avail, "pulp_total_need_kg": total_need})
    merged: dict[str, dict] = {}
    for r in results:                                     # a flavor is limited by its scarcest ingredient
        cur = merged.get(r["flavor"])
        if cur is None or r["loss_share"] > cur["loss_share"] or (r["loss_share"] == cur["loss_share"] == 0 and False):
            merged[r["flavor"]] = {**r, "binding_ingredient": r["ingredient"] if r["loss_share"] > 0 else None}
    order = {"PP": 0, "DP": 1, "DB": 2}
    results = sorted(merged.values(), key=lambda x: order.get(x["flavor"], 9))
    return {"rule": rule, "days": days, "rows": results, "route": route,
            "note": "Need = median forecast demand x kg per bottle (upper bound: plants cannot add lines). Inbound counts planned ETAs only; PF inbound is excluded while the highway is closed.",
            "provenance": ["SYNTHETIC", "ESTIMATED"]}


def pulp_expiry(run: EngineRun) -> dict:
    """E13: frozen pulp lots in plant freezers, flagged from 120 days old; shelf life is up to 180 days."""
    d, snap = stock_snapshot(run)
    if d is None:
        return {"lots": []}
    s = snap[snap.qty_kg > 1].copy()
    s["age_days"] = (run.day - s["receipt_date"]).dt.days
    s["days_to_expiry"] = (s["expiry_date"] - run.day).dt.days
    flag = run.params["pulp_expiry_flag_days"]
    s["flag"] = np.where(s["age_days"] >= 165, "RED", np.where(s["age_days"] >= flag, "AMBER", "OK"))
    names = ingredient_names(run)
    g = s.groupby(["ingredient_id", "plant_id", "lot_id", "receipt_date", "expiry_date", "age_days", "days_to_expiry", "flag"], as_index=False)["qty_kg"].sum()
    rows = [{"ingredient": r.ingredient_id, "name": names[r.ingredient_id], "plant": r.plant_id, "lot": r.lot_id,
             "received": str(r.receipt_date.date()), "expires": str(r.expiry_date.date()), "age_days": int(r.age_days),
             "days_to_expiry": int(r.days_to_expiry), "qty_kg": float(r.qty_kg), "flag": r.flag}
            for r in g.sort_values("age_days", ascending=False).itertuples()]
    return {"snapshot_date": str(d.date()), "flag_days": flag, "life_days": run.params["frozen_pulp_life_days"], "lots": rows,
            "flagged_kg": float(sum(r["qty_kg"] for r in rows if r["flag"] != "OK")), "provenance": ["SYNTHETIC", "OBSERVED"]}


def realised_price(run: EngineRun) -> dict[str, float]:
    sm = run.snap.sales_monthly
    g = sm.groupby("flavor_id")[["revenue_inr", "bottles_sold"]].sum()
    out = (g["revenue_inr"] / g["bottles_sold"].replace(0, float("nan"))).to_dict()
    return {k: v for k, v in out.items() if v == v}            # a flavor with no sales yet falls back to the flat case price


def scarce_tradeoff(run: EngineRun, pf_kg: float | None = None, pp_share: float = 0.5, economics: str = "margin") -> dict:
    """E15: what each kilogram of scarce passionfruit pulp earns as Passionfruit Punch or as Dragon Passion."""
    b = bom(run)
    prices = realised_price(run)
    mult = run.params["margin"] if economics == "margin" else 1.0
    rows = {}
    for f in ("PP", "DP"):
        k = float(b[(b.flavor_id == f) & (b.ingredient_id == "ING_PF")]["kg_per_bottle"].iloc[0])
        price = prices.get(f, run.params["price"])
        rows[f] = {"flavor": f, "kg_per_bottle": k, "bottles_per_kg": 1.0 / k, "price": price, "value_per_bottle": price * mult,
                   "value_per_kg_pf": price * mult / k}
    df_k = float(b[(b.flavor_id == "DP") & (b.ingredient_id == "ING_DF")]["kg_per_bottle"].iloc[0])
    d, snap = stock_snapshot(run)
    df_stock = float(snap[snap.ingredient_id == "ING_DF"]["qty_kg"].sum()) if d is not None else 0.0
    pf_stock = float(snap[snap.ingredient_id == "ING_PF"]["qty_kg"].sum()) if d is not None else 0.0
    pf = pf_kg if pf_kg is not None else pf_stock
    pp_kg = pf * pp_share
    dp_kg = pf * (1 - pp_share)
    pp_b = pp_kg * rows["PP"]["bottles_per_kg"]
    dp_b_pf = dp_kg * rows["DP"]["bottles_per_kg"]
    dp_b = min(dp_b_pf, df_stock / df_k)                          # DP also needs dragonfruit pulp
    out = {"economics": economics, "rows": rows, "pf_kg": pf, "pp_share": pp_share,
           "plan": {"pp_bottles": pp_b, "dp_bottles": dp_b, "pp_value": pp_b * rows["PP"]["value_per_bottle"],
                    "dp_value": dp_b * rows["DP"]["value_per_bottle"], "df_binding": bool(dp_b < dp_b_pf - 1)},
           "df_stock_kg": df_stock, "pf_stock_kg": pf_stock, "dp_per_kg_advantage": rows["DP"]["value_per_kg_pf"] / rows["PP"]["value_per_kg_pf"],
           "note": "Prices are realised price per flavor from monthly sales (SYNTHETIC); margin uses the case's flat 32% because no per-flavor margin is given (ASSUMED).",
           "provenance": ["SYNTHETIC", "CALCULATED", "ASSUMED"]}
    # sweep
    sweep = []
    for s in np.linspace(0, 1, 11):
        ppb = pf * s * rows["PP"]["bottles_per_kg"]
        dpb = min(pf * (1 - s) * rows["DP"]["bottles_per_kg"], df_stock / df_k)
        sweep.append({"pp_share": float(s), "pp_value": ppb * rows["PP"]["value_per_bottle"], "dp_value": dpb * rows["DP"]["value_per_bottle"]})
    out["sweep"] = sweep
    return out


def corridor(run: EngineRun, hazard: str | None = None, closure_days: int = 21, launch_date: str | None = None) -> dict:
    """E17: two inbound lanes. Lane 1: hill highway (3-day truck, air bridge at 5x). Lane 2: imported dragonfruit (sea, 6-8 weeks)."""
    route = route_state(run)
    if hazard is None:
        hazard = "RED" if route["status"] == "closed" else "GREEN"
    exp = closure_exposure(run, closure_days)
    hist = run.ds.refs["R05"]
    hist = hist[hist["closure_start"] < run.day + pd.Timedelta(days=1)]
    n_long = int(hist["closed_7_days_or_more"].sum())
    imp = run.ds.refs["R07"]
    cols = list(imp.columns)
    ocol, ecol = "order_date", "eta (default lead time)"
    orders = [{"id": r["order_id"], "order_date": str(r[ocol].date()), "eta": str(pd.Timestamp(r[ecol]).date()), "covers": r["covers_months"],
               "tonnes": float(r["qty_tonnes_needed (2026 mix)"])} for _, r in imp.iterrows()]
    sup = run.ds.dims["supplier"].set_index("supplier_id")
    lead = int(sup.loc["SUP08", "lead_time_days"])
    upcoming = [o for o in orders if pd.Timestamp(o["order_date"]) >= run.day]
    nxt = upcoming[0] if upcoming else None
    gate = None
    if launch_date:
        ld = pd.Timestamp(launch_date)
        gate = {"launch_date": launch_date, "notice_deadline": str((ld - pd.Timedelta(days=14)).date()),
                "import_order_needed_by": str((ld - pd.Timedelta(days=lead)).date()), "lead_days": lead,
                "covered_by_next_order": bool(nxt and pd.Timestamp(nxt["eta"]) <= ld)}
    return {"asof": str(run.day.date()), "lane1": {"supplier": "SUP06", "transit_days": 3, "hazard": hazard, "route": route,
                                                    "history_long_closures": n_long, "history_years": int(len(hist)),
                                                    "air_bridge": exp, "air_rate_per_kg": run.params["air_freight_inr_per_kg"]},
            "lane2": {"supplier": "SUP08", "lead_days_low": 42, "lead_days_high": 56, "lead_days_model": lead, "orders": orders,
                      "next_order": nxt, "marketing_gate": gate},
            "provenance": ["CASE", "SYNTHETIC"]}
