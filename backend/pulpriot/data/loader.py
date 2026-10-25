"""M0 loader: read the supplied workbook once, validate it, strip truth, clean dirty rows, expose as-of views.

The supplied workbook is the primary dataset. Nothing here regenerates or edits its values. Truth sheets and
hindsight columns are removed from the input `Dataset`; `data.truth` is the only module that re-reads them and it
is imported by eval/replay/scorecard code and the API's replay routes, never by the engine."""
from __future__ import annotations

import hashlib
import pickle
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..settings import Params, cache_dir, load_params, workbook_path
from . import schema as S
from .clean import CleanContext, clean_qc, clean_stock, summarise_audit

FLAVORS = ["MPB", "GC", "PP", "DB", "DP"]
CHANNELS = ["GT", "MT", "QC"]

DATE_COLS: dict[str, list[str]] = {
    "T01_fact_sales_monthly": ["month", "available_at"],
    "T03_fact_dispatch_weekly": ["week_start", "available_at"],
    "T05_dim_lot": ["production_date", "expiry_date", "mt_qc_cutoff_date", "available_at"],
    "T06_fact_receipts_weekly": ["week_start", "available_at"],
    "T07_fact_in_transit": ["dispatch_date", "eta", "available_at"],
    "T08_fact_plant_fg": ["week_start", "available_at"],
    "T09_fact_production": ["week_start", "available_at"],
    "T10_plan_production": ["week_start", "available_at"],
    "T11_fact_forecast_history": ["forecast_made_month", "target_month", "forecast_published_at", "actual_available_at"],
    "T12_fact_allocation_history": ["month", "available_at"],
    "T13_fact_stockout_log": ["start_date", "end_date", "available_at", "available_at_agent"],
    "T14_fact_pulp_stock": ["date", "receipt_date", "expiry_date", "available_at"],
    "T15_fact_pulp_consumption": ["week_start", "available_at"],
    "T16_fact_purchase_orders": ["order_date", "planned_eta", "eta", "available_at"],
    "T17_fact_air_freight": ["booking_date", "arrival_date", "available_at"],
    "T18_fact_writeoff_discount": ["txn_date", "available_at"],
    "T19_fact_transfer_log": ["dispatch_date", "arrival_date", "available_at"],
    "T20_log_decisions": ["decided_at"],
    "X01_fact_route_status_daily": ["date", "known_at"],
    "R05_ref_highway_log": ["closure_start", "closure_end (last closed day)"],
    "R06_ref_events": ["date"],
    "R07_ref_import_calendar": ["order_date", "eta (default lead time)"],
}

SHORT = {  # short attribute names on Dataset
    "T01_fact_sales_monthly": "sales_monthly", "T02_fact_qc_daily": "qc_daily", "T03_fact_dispatch_weekly": "dispatch_weekly",
    "T04_fact_depot_stock_weekly": "stock_weekly", "T05_dim_lot": "lots", "T06_fact_receipts_weekly": "receipts_weekly",
    "T07_fact_in_transit": "in_transit", "T08_fact_plant_fg": "plant_fg", "T09_fact_production": "production",
    "T10_plan_production": "plan_production", "T11_fact_forecast_history": "forecast_history",
    "T12_fact_allocation_history": "allocation_history", "T13_fact_stockout_log": "stockout_log",
    "T14_fact_pulp_stock": "pulp_stock", "T15_fact_pulp_consumption": "pulp_consumption",
    "T16_fact_purchase_orders": "purchase_orders", "T17_fact_air_freight": "air_freight",
    "T18_fact_writeoff_discount": "writeoff_discount", "T19_fact_transfer_log": "transfer_log",
    "T20_log_decisions": "decisions_log", "X01_fact_route_status_daily": "route_status",
}


@dataclass
class Dataset:
    """Cleaned, truth-free input tables plus dimensions and references."""
    params: Params
    dims: dict[str, pd.DataFrame]
    refs: dict[str, pd.DataFrame]
    facts: dict[str, pd.DataFrame]                 # keyed by SHORT name
    arrival: dict[str, str]                        # SHORT name -> arrival column
    audit: pd.DataFrame
    schema_report: S.SchemaReport
    dirty_fixture: pd.DataFrame
    calibration: pd.DataFrame
    readme: list[str]
    ctx: CleanContext
    source_hash: str
    load_seconds: float = 0.0
    _snap_cache: dict = field(default_factory=dict)

    # dimension helpers -------------------------------------------------------------------------------
    @property
    def depots(self) -> list[str]:
        return list(self.dims["depot"]["depot_id"])

    @property
    def regions(self) -> list[str]:
        return list(self.dims["region"]["region_id"])

    def depot_region(self) -> dict[str, str]:
        d = self.dims["depot"]
        return dict(zip(d["depot_id"], d["region_id"]))

    def depot_name(self) -> dict[str, str]:
        d = self.dims["depot"]
        return dict(zip(d["depot_id"], d["depot_name"]))

    def flavor_name(self) -> dict[str, str]:
        d = self.dims["flavor"]
        return dict(zip(d["flavor_id"], d["flavor_name"]))

    def asof(self, ts: pd.Timestamp) -> "Snapshot":
        ts = pd.Timestamp(ts)
        if ts not in self._snap_cache:
            if len(self._snap_cache) > 64:
                self._snap_cache.clear()
            self._snap_cache[ts] = Snapshot(self, ts)
        return self._snap_cache[ts]


class Snapshot:
    """Everything that had arrived by `ts`. The only door to fact tables for engine code."""

    def __init__(self, ds: Dataset, ts: pd.Timestamp):
        self.ds = ds
        self.ts = ts
        self._cache: dict[str, pd.DataFrame] = {}

    def __getattr__(self, name: str) -> pd.DataFrame:
        if name.startswith("_") or name in ("ds", "ts"):
            raise AttributeError(name)
        if name in self.ds.facts:
            if name not in self._cache:
                df = self.ds.facts[name]
                col = self.ds.arrival[name]
                self._cache[name] = df[df[col] <= self.ts]
            return self._cache[name]
        raise AttributeError(name)

    @property
    def date(self) -> pd.Timestamp:
        return self.ts.normalize()


def asof_ts(day) -> pd.Timestamp:
    """Morning-briefing convention: a planner opening the tool on `day` at noon sees everything that arrived by then."""
    return pd.Timestamp(day).normalize() + pd.Timedelta(hours=12)


# ---------------------------------------------------------------------------------------------------------
def _signature(path: Path) -> str:
    st = path.stat()
    return hashlib.sha1(f"{path.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]


def read_raw_sheets(path: Path | None = None) -> tuple[dict[str, pd.DataFrame], str]:
    """All sheets as raw DataFrames, cached on disk keyed by file signature."""
    path = path or workbook_path()
    if not path.exists():
        raise FileNotFoundError(f"Workbook not found: {path}. Set PULPRIOT_WORKBOOK or place it in the project root.")
    sig = _signature(path)
    cache = cache_dir() / f"sheets_{sig}.pkl"
    if cache.exists():
        with cache.open("rb") as f:
            return pickle.load(f), sig
    sheets = pd.read_excel(path, sheet_name=None)
    with cache.open("wb") as f:
        pickle.dump(sheets, f, protocol=pickle.HIGHEST_PROTOCOL)
    return sheets, sig


def _to_dt(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def build_dataset(sheets: dict[str, pd.DataFrame], sig: str, params: Params | None = None) -> Dataset:
    t0 = time.time()
    params = params or load_params()
    rep = S.validate_workbook(sheets)
    rep.raise_if_failed()

    dims = {n.split("_", 1)[1].replace("dim_", "").replace("lane_", "lane_"): sheets[n].copy() for n in S.MASTER_SHEETS}
    dims = {  # explicit names
        "flavor": sheets["M01_dim_flavor"].copy(), "channel": sheets["M02_dim_channel"].copy(),
        "region": sheets["M03_dim_region"].copy(), "depot": sheets["M04_dim_depot"].copy(),
        "plant": sheets["M05_dim_plant"].copy(), "ingredient": sheets["M06_dim_ingredient"].copy(),
        "supplier": sheets["M07_dim_supplier"].copy(), "bom": sheets["M08_bom_recipe"].copy(),
        "lane_depot": sheets["M09_lane_depot_transfer"].copy(), "lane_plant": sheets["M10_lane_plant_depot"].copy(),
        "route": sheets["M11_route_inbound"].copy(), "synonym": sheets["M12_dim_synonym"].copy(),
        "stakeholder": sheets["M13_dim_stakeholder"].copy(), "calendar": sheets["M14_dim_calendar"].copy(),
    }
    refs = {n[:3]: _to_dt(sheets[n].copy(), DATE_COLS.get(n, [])) for n in S.REFERENCE_SHEETS}
    refs["R01"]["month"] = pd.to_datetime(refs["R01"]["month"], format="%Y-%m")

    ctx = CleanContext.build(dims["flavor"], dims["depot"], dims["synonym"], params)

    facts: dict[str, pd.DataFrame] = {}
    arrival: dict[str, str] = {}
    audit_parts: list[pd.DataFrame] = []

    for sheet, short in SHORT.items():
        df = sheets[sheet].copy()
        drop = [c for c in S.HINDSIGHT_COLUMNS.get(sheet, ()) if c in df.columns]
        df = df.drop(columns=drop)                                    # truth isolation at the door
        if sheet == "T01_fact_sales_monthly":
            df["month"] = pd.to_datetime(df["month"], format="%Y-%m")
            df["available_at"] = pd.to_datetime(df["available_at"])
        elif sheet == "T12_fact_allocation_history":
            df["month"] = pd.to_datetime(df["month"], format="%Y-%m")
            df["available_at"] = pd.to_datetime(df["available_at"])
        elif sheet == "T11_fact_forecast_history":
            for c in ("forecast_made_month", "target_month"):
                df[c] = pd.to_datetime(df[c], format="%Y-%m")
            df["forecast_published_at"] = pd.to_datetime(df["forecast_published_at"])
            df["actual_available_at"] = pd.to_datetime(df["actual_available_at"])
        elif sheet == "T02_fact_qc_daily":
            df = df.drop(columns=["available_at_marketing", "available_at_planner_as_was"], errors="ignore")
            df, aud = clean_qc(df, ctx)
            audit_parts.append(aud)
        elif sheet == "T04_fact_depot_stock_weekly":
            df, aud = clean_stock(df, ctx)
            df["production_date"] = pd.to_datetime(df["production_date"], errors="coerce")
            audit_parts.append(aud)
        else:
            df = _to_dt(df, DATE_COLS.get(sheet, []))
        facts[short] = df.reset_index(drop=True)
        arrival[short] = S.ARRIVAL_COLUMN[sheet]

    audit = pd.concat([a for a in audit_parts if len(a)], ignore_index=True) if any(len(a) for a in audit_parts) else \
        pd.DataFrame(columns=["table", "row_ref", "defect", "action", "reason", "raw"])

    readme = [str(x) for x in sheets["README"].iloc[:, 0].dropna().tolist()]
    return Dataset(params=params, dims=dims, refs=refs, facts=facts, arrival=arrival, audit=audit, schema_report=rep,
                   dirty_fixture=sheets["T22_test_dirty_rows"].copy(), calibration=sheets["Calibration_Results"].copy(),
                   readme=readme, ctx=ctx, source_hash=sig, load_seconds=time.time() - t0)


@lru_cache(maxsize=1)
def get_dataset() -> Dataset:
    sheets, sig = read_raw_sheets()
    return build_dataset(sheets, sig)


def audit_summary(ds: Dataset) -> list[dict]:
    return summarise_audit(ds.audit)
