"""FastAPI application. Every route reads from the service layer; none of them computes numbers on its own."""
from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..actions import store as ST
from ..agent import ask as AG
from ..data.loader import FLAVORS
from ..finance import impact as FI
from ..inventory.policy import fefo_plan
from ..settings import MODEL_VERSION, PROJECT_ROOT
from ..supply import capacity as CA
from ..supply import pulp as PU
from . import precompute as PC
from . import schemas as S
from . import views_control as VC
from . import views_demand as VD
from . import views_inventory as VI
from . import views_replay as VR
from .service import ASOF_DEFAULT, ASOF_MAX, ASOF_MIN, get_service
from .util import clean

app = FastAPI(title="Pulp Riot supply chain agent", version=MODEL_VERSION)
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
svc = get_service()


@app.middleware("http")
async def invalidate_on_write(request, call_next):
    resp = await call_next(request)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api"):
        PC.bump()                                    # stored Control tower payloads carry approval state
    return resp


def out(data) -> JSONResponse:
    return JSONResponse(clean(data))


def day_q(asof: str | None) -> pd.Timestamp:
    try:
        return pd.Timestamp(asof) if asof else ASOF_DEFAULT
    except Exception as e:                                                   # noqa: BLE001
        raise HTTPException(422, f"asof must be a date (YYYY-MM-DD): {e}")


@app.exception_handler(Exception)
async def all_errors(_, exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    return JSONResponse({"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc().splitlines()[-4:]}, status_code=500)


@app.on_event("startup")
def warm() -> None:
    def work() -> None:
        try:
            ct_cached(ASOF_DEFAULT, "margin")
        except Exception:                                                    # noqa: BLE001
            pass
        try:
            VR.replay_cache(svc).ensure_started()
        except Exception:                                                    # noqa: BLE001
            pass
        try:
            PC.run_all(ASOF_DEFAULT, ASOF_MIN, ASOF_MAX, workers=3)
        except Exception:                                                    # noqa: BLE001
            pass
    threading.Thread(target=work, daemon=True).start()


# ---- meta / trust -----------------------------------------------------------------------------------------------
@app.get("/api/meta", response_model=S.Meta, response_model_exclude_unset=False)
def meta():
    m = svc.meta()
    return JSONResponse(clean(m))


@app.get("/api/params")
def params():
    return out({"rows": svc.ds.params.table(), "config_hash": svc.ds.params.config_hash})


@app.get("/api/trust")
def trust(asof: str | None = None):
    return out(svc.trust(day_q(asof)))


# ---- control tower -----------------------------------------------------------------------------------------------
def ct_cached(day, economics: str) -> Response:
    """Control tower payload from the disk cache when a worker already computed it, else computed now and stored."""
    day = pd.Timestamp(day)
    body = PC.read(day, economics)
    if body is None:
        v0 = PC.version()
        body = json.dumps(clean(VC.control_tower(svc, day, economics))).encode()
        if PC.version() == v0:
            PC.write(day, body, economics)
    return Response(body, media_type="application/json")


@app.get("/api/control-tower")
def control_tower(asof: str | None = None, economics: str = "margin"):
    return ct_cached(day_q(asof), economics)


@app.get("/api/heatmap")
def heatmap(asof: str | None = None):
    return out(VC.heatmap(svc, day_q(asof)))


@app.get("/api/sankey")
def sankey(asof: str | None = None):
    return out(VC.sankey(svc, day_q(asof)))


@app.get("/api/rebalancing")
def rebalancing(asof: str | None = None):
    return out(VC.rebalancing(svc, day_q(asof)))


@app.get("/api/stock-check")
def stock_check(flavor: str, depot: str = "ALL", channel: str = "ALL", asof: str | None = None):
    return out(VC.stock_check(svc, day_q(asof), flavor, depot, channel))


@app.get("/api/weekly-review")
def weekly_review(asof: str | None = None, economics: str = "margin"):
    return out(VC.weekly_review(svc, day_q(asof), economics))


@app.post("/api/ask")
def ask(body: S.AskIn):
    return out(AG.answer(svc, day_q(body.asof), body.question))


# ---- demand --------------------------------------------------------------------------------------------------------
@app.get("/api/demand/forecast")
def forecast(flavor: str, asof: str | None = None, depot: str | None = None, region: str | None = None, channel: str = "ALL", reveal: bool = False):
    return out(VD.forecast_view(svc, day_q(asof), flavor, depot, region, channel, reveal))


@app.get("/api/demand/qc-monitor")
def qc_monitor(asof: str | None = None, flavor: str | None = None):
    return out(VD.qc_monitor(svc, day_q(asof), flavor))


@app.get("/api/demand/spikes")
def spikes(asof: str | None = None, flavor: str | None = None, rho: float | None = Query(None, ge=0, le=1)):
    return out(VD.spike_view(svc, day_q(asof), flavor, rho))


@app.get("/api/demand/explainer")
def explainer(asof: str | None = None, flavor: str | None = None):
    return out(VD.explainer(svc, day_q(asof), flavor))


@app.post("/api/demand/launch")
def launch(body: S.LaunchIn):
    return out(VD.launch_view(svc, day_q(body.asof), body.flavor, body.similarity, body.scale, body.hypothetical))


@app.get("/api/demand/campaigns")
def campaigns(asof: str | None = None):
    return out(VD.campaigns_view(svc, day_q(asof)))


@app.post("/api/demand/campaigns")
def add_campaign(body: S.CampaignIn):
    return out(ST.add_campaign(body.model_dump()))


@app.get("/api/demand/accuracy")
def accuracy():
    return out(VD.accuracy_view(svc))


# ---- inventory -----------------------------------------------------------------------------------------------------
@app.get("/api/inventory/table")
def inv_table(asof: str | None = None):
    return out(VI.stock_table(svc, day_q(asof)))


@app.get("/api/inventory/tank")
def inv_tank(flavor: str, depot: str, asof: str | None = None):
    return out(VI.tank(svc, day_q(asof), flavor, depot))


@app.get("/api/inventory/cover")
def inv_cover(flavor: str, depot: str, asof: str | None = None, surge: float = Query(1.0, ge=1, le=4), rho: float | None = Query(None, ge=0, le=1)):
    return out(VI.cover_view(svc, day_q(asof), flavor, depot, surge, rho))


@app.get("/api/inventory/safety")
def inv_safety(flavor: str, depot: str, asof: str | None = None, service_level: float | None = Query(None, ge=0.5, le=0.999)):
    return out(VI.safety_view(svc, day_q(asof), flavor, depot, service_level))


@app.get("/api/inventory/sellable")
def inv_sellable(flavor: str, depot: str, asof: str | None = None):
    return out(VI.sellable_view(svc, day_q(asof), flavor, depot))


@app.post("/api/inventory/transfer-preview")
def inv_transfer(body: S.TransferIn):
    return out(VI.transfer_view(svc, day_q(body.asof), body.flavor, body.donor, body.recipient, body.qty))


@app.get("/api/inventory/shift")
def inv_shift(asof: str | None = None):
    return out(VI.shift_view(svc, day_q(asof)))


@app.get("/api/inventory/domino")
def inv_domino(flavor: str, depot: str, asof: str | None = None):
    return out(VI.domino(svc, day_q(asof), flavor, depot))


@app.get("/api/inventory/surge")
def inv_surge(flavor: str, multiplier: float = Query(2.0, ge=1, le=4), asof: str | None = None):
    return out(VI.surge_view(svc, day_q(asof), flavor, multiplier))


@app.get("/api/inventory/delay-clock")
def inv_delay(asof: str | None = None, flavor: str | None = None, economics: str = "margin"):
    return out(VI.delay_clock(svc, day_q(asof), flavor, economics))


@app.get("/api/inventory/mitigation")
def inv_mitigation(flavor: str, depot: str, asof: str | None = None, economics: str = "margin"):
    return out(VI.mitigation_view(svc, day_q(asof), flavor, depot, economics))


# ---- supply ----------------------------------------------------------------------------------------------------------
@app.get("/api/supply/runway")
def sup_runway(asof: str | None = None):
    run = svc.run(day_q(asof))
    return out({"asof": str(run.day.date()), "rows": PU.pulp_status(run, 300), "exposure": {d: PU.closure_exposure(run, d) for d in (7, 14, 21)}})


@app.get("/api/supply/conflict")
def sup_conflict(asof: str | None = None, rule: str = "pro_rata", days: int = Query(28, ge=7, le=56)):
    return out(PU.conflict(svc.run(day_q(asof)), rule, days))


@app.get("/api/supply/capacity")
def sup_capacity(asof: str | None = None):
    return out(CA.capacity_options(svc.run(day_q(asof))))


@app.get("/api/supply/copacker-deadline")
def sup_deadline(asof: str | None = None):
    return out(CA.copacker_deadline(svc.run(day_q(asof)), VC.grid(svc, day_q(asof))))


@app.get("/api/supply/reprioritiser")
def sup_mix(asof: str | None = None):
    return out(CA.reprioritiser(svc.run(day_q(asof))))


@app.get("/api/supply/expiry")
def sup_expiry(asof: str | None = None):
    return out(PU.pulp_expiry(svc.run(day_q(asof))))


@app.get("/api/supply/tradeoff")
def sup_tradeoff(asof: str | None = None, pf_kg: float | None = None, pp_share: float = Query(0.5, ge=0, le=1), economics: str = "margin"):
    return out(PU.scarce_tradeoff(svc.run(day_q(asof)), pf_kg, pp_share, economics))


@app.get("/api/supply/corridor")
def sup_corridor(asof: str | None = None, hazard: str | None = None, closure_days: int = Query(21, ge=1, le=60), launch_date: str | None = None):
    return out(PU.corridor(svc.run(day_q(asof)), hazard, closure_days, launch_date))


# ---- finance -----------------------------------------------------------------------------------------------------------
@app.get("/api/finance/waterfall")
def waterfall(asof: str | None = None, economics: str = "margin"):
    run = svc.run(day_q(asof))
    acts = [a for a in VC.actions_state(svc, day_q(asof)) if a["active"] and a["status_at_asof"] in ("PENDING", "APPROVED", "MODIFIED")]
    return out(FI.waterfall(run, acts, economics))


# ---- decisions -----------------------------------------------------------------------------------------------------------
@app.get("/api/actions")
def actions(asof: str | None = None):
    return out({"asof": str(day_q(asof).date()), "rows": VC.actions_state(svc, day_q(asof)), "roles": svc.meta()["roles"]})


@app.get("/api/alerts")
def alerts(asof: str | None = None):
    return out(VR.alerts_view(svc, day_q(asof)))


@app.post("/api/actions/{action_id:path}/decision")
def decision(action_id: str, body: S.DecisionIn):
    try:
        row = VR.decide(svc, action_id, body.decision, body.actor_role, body.asof, body.reason, body.qty)
    except KeyError:
        raise HTTPException(404, "Unknown action")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return out({"action": row, "note": "Decision saved. Nothing was executed: purchasing, production, transfers and messages stay with the people involved."})


@app.get("/api/audit")
def audit(action_id: str | None = None):
    return out({"rows": ST.audit(action_id)})


@app.get("/api/decisions-history")
def decisions_history():
    return out(VR.history_log(svc))


@app.post("/api/demo/reset")
def demo_reset():
    ST.reset_all()
    return out({"ok": True, "note": "Cleared decisions, audit log and entered campaigns (demo storage only)."})


# ---- replay --------------------------------------------------------------------------------------------------------------
@app.get("/api/replay/timeline")
def replay_timeline(reveal: bool = False):
    return out(VR.timeline(svc, reveal))


@app.get("/api/replay/series")
def replay_series(asof: str | None = None, flavor: str = "DP", region: str = "ALL", reveal: bool = False):
    return out(VR.series(svc, day_q(asof), flavor, region, reveal))


@app.get("/api/replay/state")
def replay_state(asof: str | None = None):
    return out(VR.state(svc, day_q(asof)))


@app.get("/api/replay/scenarios")
def replay_scenarios():
    return out(svc.job("scenarios", lambda: VR.scenarios(svc)))


@app.get("/api/replay/scorecard")
def replay_scorecard():
    return out(svc.job("scorecard", lambda: VR.scorecard(svc)))


@app.get("/api/health")
def health():
    return {"ok": True, "model_version": MODEL_VERSION}


# ---- static front end (built) ------------------------------------------------------------------------------------------------
DIST = PROJECT_ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
