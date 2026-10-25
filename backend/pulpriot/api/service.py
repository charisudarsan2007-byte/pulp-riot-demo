"""Service layer: dataset singleton, per-as-of EngineRun cache, background jobs, metadata and the trust summary (A5)."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..data.loader import Dataset, FLAVORS, get_dataset
from ..data.clean import CleanContext, clean_qc, clean_stock
from ..engine.monthly import summer_share_check
from ..engine.pipeline import EngineRun
from ..settings import MODEL_VERSION, Params, load_params, workbook_path
from ..tags import trust_class

ASOF_MIN = pd.Timestamp("2026-03-01")       # start of the summer window
ASOF_MAX = pd.Timestamp("2026-06-30")       # end of the summer window
ASOF_DEFAULT = pd.Timestamp("2026-03-24")   # first full day of the Dragon Passion stockouts, planners still unaware
AWARENESS = pd.Timestamp("2026-04-06")      # R06 EV05: as-was planner awareness (replay label, SYNTHETIC)


def clamp_day(d) -> pd.Timestamp:
    d = pd.Timestamp(d).normalize()
    return min(max(d, ASOF_MIN), ASOF_MAX)


class Service:
    def __init__(self) -> None:
        self.ds: Dataset = get_dataset()
        self._runs: "OrderedDict[tuple, EngineRun]" = OrderedDict()
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._memo: dict[tuple, Any] = {}

    # ---- runs -------------------------------------------------------------------------------------------------
    def run(self, day=None, params: Params | None = None, similarity: dict | None = None, scale: float = 1.0) -> EngineRun:
        d = clamp_day(day if day is not None else ASOF_DEFAULT)
        p = params or self.ds.params
        key = (str(d.date()), p.config_hash, repr(sorted((similarity or {}).items())), scale)
        with self._lock:
            if key in self._runs:
                self._runs.move_to_end(key)
                return self._runs[key]
            r = EngineRun(self.ds, d, p, similarity, scale)
            self._runs[key] = r
            while len(self._runs) > 10:
                self._runs.popitem(last=False)
            return r

    def with_rho(self, day, rho: float | None) -> EngineRun:
        if rho is None or rho == self.ds.params["pass_through_rho"]:
            return self.run(day)
        p = self.ds.params.with_overrides(pass_through_rho=float(rho))
        return self.run(day, params=p)

    def memo(self, key: tuple, fn: Callable[[], Any]) -> Any:
        with self._lock:
            if key in self._memo:
                return self._memo[key]
        val = fn()
        with self._lock:
            if len(self._memo) > 400:
                self._memo.clear()
            self._memo[key] = val
        return val

    # ---- jobs -------------------------------------------------------------------------------------------------
    def job(self, key: str, fn: Callable[[], Any]) -> dict:
        """Start fn in a thread on first request; later calls return its status or result."""
        with self._lock:
            j = self._jobs.get(key)
            if j is None:
                j = {"status": "running", "started": time.time(), "result": None, "error": None}
                self._jobs[key] = j

                def work() -> None:
                    try:
                        j["result"] = fn()
                        j["status"] = "done"
                    except Exception as e:                                   # noqa: BLE001
                        j["status"] = "error"
                        j["error"] = f"{type(e).__name__}: {e}"

                threading.Thread(target=work, daemon=True).start()
            return {"status": j["status"], "result": j["result"], "error": j["error"], "elapsed": time.time() - j["started"]}

    # ---- metadata ---------------------------------------------------------------------------------------------
    def meta(self) -> dict:
        ds = self.ds
        fl = ds.dims["flavor"]
        dp = ds.dims["depot"]
        from ..actions import roles as RL
        return {
            "model_version": MODEL_VERSION, "config_hash": ds.params.config_hash, "source_hash": ds.source_hash,
            "workbook": workbook_path().name,
            "asof": {"min": str(ASOF_MIN.date()), "max": str(ASOF_MAX.date()), "default": str(ASOF_DEFAULT.date())},
            "flavors": [{"id": r.flavor_id, "name": r.flavor_name, "status": r.status, "launch_date": str(r.launch_date)} for r in fl.itertuples()],
            "depots": [{"id": r.depot_id, "name": r.depot_name, "region": r.region_id, "metro": bool(r.metro_flag), "primary_plant": r.primary_plant_id}
                       for r in dp.itertuples()],
            "regions": [{"id": r.region_id, "name": r.region_name} for r in ds.dims["region"].itertuples()],
            "channels": [{"id": "GT", "name": "General trade"}, {"id": "MT", "name": "Modern trade"}, {"id": "QC", "name": "Quick commerce"}],
            "roles": RL.stakeholders(ds),
            "price": ds.params["price"], "margin": ds.params["margin"],
            "labels": {"mode": "SYNTHETIC REPLAY", "note": "All data is the supplied synthetic workbook. Dates are a replay: nothing here is live."},
            "events": [{"id": r.event_id, "date": str(r.date.date()), "type": r.event_type, "flavor": r.flavor_id, "description": r.description}
                       for r in ds.refs["R06"].itertuples()],
        }

    # ---- trust (A5) -------------------------------------------------------------------------------------------
    def trust(self, day) -> dict:
        run = self.run(day)
        ds, p = self.ds, self.ds.params
        snap, ts = run.snap, run.ts
        sources = []
        limits = {"qc_daily": ("Quick-commerce daily feed", p["stale_qc_days"]), "stock_weekly": ("Depot stock reports (weekly)", p["stale_depot_days"]),
                  "sales_monthly": ("Monthly sales", p["stale_monthly_days"]), "dispatch_weekly": ("Depot dispatch (weekly)", p["stale_depot_days"]),
                  "receipts_weekly": ("Depot receipts (weekly)", p["stale_depot_days"]), "pulp_stock": ("Plant pulp stock (daily)", 3),
                  "in_transit": ("Shipments in transit", 3), "plant_fg": ("Plant finished goods (weekly)", 9), "route_status": ("Highway status (daily)", 2)}
        for key, (label, lim) in limits.items():
            df = getattr(snap, key)
            if df.empty:
                sources.append({"key": key, "label": label, "latest_arrival": None, "age_days": None, "limit_days": lim, "stale": True, "rows": 0})
                continue
            col = ds.arrival[key]
            latest = df[col].max()
            age = (ts - latest).total_seconds() / 86400.0
            sources.append({"key": key, "label": label, "latest_arrival": str(latest), "age_days": round(age, 2), "limit_days": lim,
                            "stale": bool(age > lim), "rows": int(len(df))})
        audit = ds.audit
        summary = []
        if len(audit):
            summary = audit.groupby(["table", "defect", "action"]).size().reset_index(name="rows").to_dict("records")
        # the supplied dirty-row fixture (T22): run the cleaners over every fixture row appended to the real tables
        fixture = self._dirty_fixture_result()
        w = run.weekly
        gaps = int(w["balance_gap"].sum()) if len(w) else 0
        cens = {}
        for fl in FLAVORS:
            s = run.series.get((fl, "ALL"))
            cens[fl] = float((s.cens[-28:] >= 0.5).mean()) if s is not None and len(s.cens) else 0.0
        summer = summer_share_check(run.mf)
        return {
            "asof": str(run.day.date()), "arrival_cutoff": str(ts), "sources": sources, "cleaning": summary,
            "cleaning_note": "The supplied tables contain no embedded defects; the dirty-row fixture sheet (T22) is run through the same cleaners in the self-test below.",
            "fixture": fixture, "balance_gap_weeks": gaps, "censored_share_28d": cens,
            "censored_limit": p["censored_fraction_limit"], "summer_share_check": summer,
            "signal": {"detection": "units_sold", "orders_on_company_present": False,
                       "note": "The quick-commerce feed has no uncensored orders column. Detection uses units sold and skips stockout-flagged days; outputs carry UNITS_SOLD_SIGNAL."},
            "missing_inputs": ["orders_on_company (QC)", "alloc_capped (depot receipts)", "fact_launch_plan (marketing planned week-4 units)",
                               "platform split of QC (Blinkit, Zepto, Swiggy)", "approval value limits (PAR100)"],
            "hindsight_removed": [{"table": t, "columns": list(c)} for t, c in __import__("pulpriot.data.schema", fromlist=["HINDSIGHT_COLUMNS"]).HINDSIGHT_COLUMNS.items()],
            "truth_sheets_not_used": list(__import__("pulpriot.data.schema", fromlist=["TRUTH_SHEETS"]).TRUTH_SHEETS),
            "provenance": ["CALCULATED", "SYNTHETIC"],
            "badge_legend": {"REPORTED": "A figure reported by a source system.", "CALCULATION": "Arithmetic on reported figures.",
                             "ESTIMATE": "A model estimate; carries an uncertainty type.", "ASSUMPTION": "A value the case does not give; configurable."},
        }

    def _dirty_fixture_result(self) -> dict:
        key = ("fixture",)
        if key in self._memo:
            return self._memo[key]
        import ast
        ds = self.ds
        fx = ds.dirty_fixture
        sheets, _ = __import__("pulpriot.data.loader", fromlist=["read_raw_sheets"]).read_raw_sheets()
        raw_qc = sheets["T02_fact_qc_daily"].drop(columns=["demand_true", "available_at_marketing", "available_at_planner_as_was"], errors="ignore")
        raw_st = sheets["T04_fact_depot_stock_weekly"]
        results = []
        for target, base, cleaner in (("fact_qc_daily", raw_qc, clean_qc), ("fact_depot_stock_weekly", raw_st, clean_stock)):
            rows = fx[fx.target_dataset == target]
            inj = []
            for _, r in rows.iterrows():
                d = ast.literal_eval(r["raw_values"])
                inj.append({k: d.get(k) for k in base.columns if k in d})
            extra = pd.DataFrame(inj)
            for c in base.columns:
                if c not in extra.columns:
                    extra[c] = None
            combined = pd.concat([base, extra[base.columns]], ignore_index=True)
            _, aud = cleaner(combined, ds.ctx)
            aud = aud[aud["row_ref"] >= len(base)].copy()
            aud["fixture_row"] = aud["row_ref"] - len(base)
            by_row = {}
            for a in aud.itertuples():
                by_row.setdefault(int(a.fixture_row), []).append(a.action)
            for i, (_, r) in enumerate(rows.iterrows()):
                results.append({"row_id": r["row_id"], "target": target, "defect": r["defect_type"], "expected": r["expected_handling"],
                                "handled": ", ".join(by_row.get(i, ["NOT HANDLED"]))})
        ok = sum(1 for x in results if x["handled"] != "NOT HANDLED")
        out = {"rows": results, "handled": ok, "total": len(results)}
        self._memo[key] = out
        return out


_SERVICE: Service | None = None


def get_service() -> Service:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = Service()
    return _SERVICE
