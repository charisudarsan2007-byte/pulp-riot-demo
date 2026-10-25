"""Financial impact of the recommended actions (F2 / F4), with a revenue / contribution-margin switch.

Red (do nothing): stockout lost value + overstock write-offs/discounts + emergency air freight (the premium over normal freight).
Green (act): losses the actions avoid, less mitigation costs (INR 1.50/bottle transfers, plant freight, co-packer premium).
Net rupees saved = do-nothing loss - (residual loss + mitigation cost).

No double counting: one action per depot-flavor feeds the stockout bars, avoided loss comes from the same simulated paths as
the do-nothing loss, and pulp air freight is counted only once, at its premium over the normal rate."""
from __future__ import annotations

from ..engine.pipeline import EngineRun


def waterfall(run: EngineRun, recs: list[dict], economics: str = "margin", only_open: bool = True) -> dict:
    p = run.params
    mult = p["margin"] if economics == "margin" else 1.0
    price = p["price"]
    stock_loss = 0.0
    avoided = 0.0
    transfer_cost = plant_cost = copacker_cost = 0.0
    overstock = 0.0
    air_premium = 0.0
    air_cost = 0.0
    lines = []
    for r in recs:
        if only_open and r["status"] in ("REJECTED",):
            continue
        pl = r.get("payload") or {}
        t = r["type"]
        if t in ("rebalance", "plant", "copacker"):
            opts = pl.get("options") or []
            nothing = next((o for o in opts if o["key"] == "nothing"), None)
            chosen = next((o for o in opts if o["key"] == {"rebalance": "transfer", "plant": "plant", "copacker": "copacker"}[t]), None)
            lost_mean = (nothing or {}).get("bottles_at_risk", 0.0)
            av = (chosen or {}).get("avoided_lost_bottles_mean", 0.0)
            stock_loss += lost_mean * price * mult
            avoided += av * price * mult
            cost = r.get("cost_inr") or 0.0
            if t == "rebalance":
                transfer_cost += cost
            elif t == "plant":
                plant_cost += cost
            else:
                copacker_cost += cost
            lines.append({"id": r["id"], "type": t, "do_nothing_loss": lost_mean * price * mult, "avoided": av * price * mult, "cost": cost})
        elif t == "fefo_redirect":
            overstock += (r.get("exposure_inr") or 0.0)
            lines.append({"id": r["id"], "type": t, "do_nothing_loss": r.get("exposure_inr") or 0.0, "avoided": r.get("exposure_inr") or 0.0 * 0.0, "cost": 0.0})
        elif t in ("air_freight", "pulp_buffer"):
            ex = pl.get("exposure") or {}
            prem = ex.get("air_cost_inr", 0.0) - ex.get("normal_cost_inr", 0.0)
            air_premium += prem
            air_cost += r.get("cost_inr") or 0.0
            lines.append({"id": r["id"], "type": t, "do_nothing_loss": prem, "avoided": 0.0, "cost": r.get("cost_inr") or 0.0})
    do_nothing = stock_loss + overstock + air_premium
    mitigation = transfer_cost + plant_cost + copacker_cost
    residual = max(stock_loss - avoided, 0.0) + overstock + air_premium
    net = do_nothing - (residual + mitigation)
    return {
        "economics": economics, "unit_note": "contribution margin (price x 32%)" if economics == "margin" else "gross revenue (price x bottles)",
        "do_nothing": {"stockout_lost_value": stock_loss, "overstock_writeoff": overstock, "emergency_air_freight": air_premium, "total": do_nothing},
        "actions": {"loss_avoided": avoided, "transfer_cost": transfer_cost, "plant_freight": plant_cost, "copacker_premium": copacker_cost,
                    "mitigation_total": mitigation},
        "residual_loss": residual, "net_saved": net, "lines": lines, "count": len(lines),
        "formula": "net saved = do-nothing loss - (residual loss after actions + mitigation costs); avoided loss from the same sample paths as the do-nothing loss",
        "notes": ["Overstock exposure is counted at the average discount cost per bottle from the discount log; it is not assumed avoided by the action unless approved and executed.",
                  "Air freight counts only the premium over the normal rate; the pulp itself would be bought anyway.",
                  "These are model estimates under uncalibrated scenario intervals, not measured savings."],
        "provenance": ["SYNTHETIC", "ESTIMATED", "CALCULATED"],
    }
