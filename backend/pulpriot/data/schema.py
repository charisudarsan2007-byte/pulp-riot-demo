"""Workbook schema: which sheets exist, which columns each must carry, and which are truth-only.

The expected columns come from the workbook's own Table_Index sheet, so a renamed or dropped column fails loudly
with the sheet and column named. Truth isolation is enforced here: truth sheets and hindsight columns never
enter the input `Dataset` (see loader.py) and the engine packages never import `data.truth`."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


class SchemaError(Exception):
    """A mandatory sheet or column is missing from the workbook."""


# Sheets that hold hidden truth, labels or scenario outcomes. Validation and replay scoring code only.
TRUTH_SHEETS = (
    "V01_truth_demand_daily", "V02_truth_stock_daily",
    "L01_label_daily", "L02_label_daily_supply", "L03_label_events",
    "Scenario_Flag_Truth", "Scenario_Catalog",
)

# Columns that sit inside input tables but are hindsight / truth. Dropped at load, kept only for truth access.
HINDSIGHT_COLUMNS: dict[str, tuple[str, ...]] = {
    "T02_fact_qc_daily": ("demand_true",),
    "T03_fact_dispatch_weekly": ("demand_true",),
    "T07_fact_in_transit": ("status_as_of_2026_08_31",),
    "T11_fact_forecast_history": ("actual_demand_est", "abs_pct_error"),
    # realised ETA, delay and status are only known after the fact; the planned ETA is what a planner has on order day
    "T16_fact_purchase_orders": ("eta", "delay_days", "status"),
}

# Arrival column per input table (a row may be used only at or after this timestamp).
ARRIVAL_COLUMN: dict[str, str] = {
    "T01_fact_sales_monthly": "available_at",
    "T02_fact_qc_daily": "available_at_agent",
    "T03_fact_dispatch_weekly": "available_at",
    "T04_fact_depot_stock_weekly": "report_received_at",
    "T05_dim_lot": "available_at",
    "T06_fact_receipts_weekly": "available_at",
    "T07_fact_in_transit": "available_at",
    "T08_fact_plant_fg": "available_at",
    "T09_fact_production": "available_at",
    "T10_plan_production": "available_at",
    "T11_fact_forecast_history": "forecast_published_at",
    "T12_fact_allocation_history": "available_at",
    "T13_fact_stockout_log": "available_at_agent",
    "T14_fact_pulp_stock": "available_at",
    "T15_fact_pulp_consumption": "available_at",
    "T16_fact_purchase_orders": "available_at",
    "T17_fact_air_freight": "available_at",
    "T18_fact_writeoff_discount": "available_at",
    "T19_fact_transfer_log": "available_at",
    "T20_log_decisions": "decided_at",
    "X01_fact_route_status_daily": "known_at",
}

MASTER_SHEETS = (
    "M01_dim_flavor", "M02_dim_channel", "M03_dim_region", "M04_dim_depot", "M05_dim_plant",
    "M06_dim_ingredient", "M07_dim_supplier", "M08_bom_recipe", "M09_lane_depot_transfer",
    "M10_lane_plant_depot", "M11_route_inbound", "M12_dim_synonym", "M13_dim_stakeholder", "M14_dim_calendar",
)
REFERENCE_SHEETS = (
    "R01_ref_monthly_volume", "R02_ref_depot_flavor_mix", "R03_ref_analog_curve", "R04_ref_spike_shape",
    "R05_ref_highway_log", "R06_ref_events", "R07_ref_import_calendar", "R08_ref_budget", "R09_ref_candidates",
)
INPUT_FACT_SHEETS = tuple(ARRIVAL_COLUMN) + ("T21_log_usage", "T22_test_dirty_rows")
DOC_SHEETS = ("README", "Calibration_Results", "Generator_Settings", "Table_Index")

# Mandatory columns per input table: what this application reads. Missing -> SchemaError; other Table_Index
# columns that are absent only raise a warning.
MANDATORY_COLUMNS: dict[str, tuple[str, ...]] = {
    "T01_fact_sales_monthly": ("month", "flavor_id", "depot_id", "channel_id", "bottles_sold", "revenue_inr", "available_at"),
    "T02_fact_qc_daily": ("date", "flavor_id", "depot_id", "region_id", "units_sold", "stockout_flag", "available_at_agent"),
    "T03_fact_dispatch_weekly": ("week_start", "depot_id", "flavor_id", "channel_id", "bottles_dispatched", "available_at"),
    "T04_fact_depot_stock_weekly": ("stock_date", "depot_id", "flavor_id", "lot_id", "qty_bottles", "production_date", "report_received_at"),
    "T05_dim_lot": ("lot_id", "flavor_id", "plant_id", "production_date", "qty_bottles", "expiry_date", "mt_qc_cutoff_date"),
    "T06_fact_receipts_weekly": ("week_start", "depot_id", "flavor_id", "source_type", "bottles_received", "available_at"),
    "T07_fact_in_transit": ("shipment_id", "movement", "origin_id", "destination_id", "flavor_id", "qty_bottles", "dispatch_date", "eta", "available_at"),
    "T08_fact_plant_fg": ("week_start", "plant_id", "flavor_id", "fg_bottles", "available_at"),
    "T09_fact_production": ("week_start", "plant_id", "flavor_id", "planned_bottles", "actual_bottles", "available_at"),
    "T12_fact_allocation_history": ("month", "depot_id", "flavor_id", "allocated_bottles", "available_at"),
    "T13_fact_stockout_log": ("episode_id", "depot_id", "flavor_id", "channel_id", "start_date", "end_date", "lost_units_est", "available_at_agent"),
    "T14_fact_pulp_stock": ("date", "plant_id", "ingredient_id", "lot_id", "qty_kg", "receipt_date", "expiry_date", "available_at"),
    "T15_fact_pulp_consumption": ("week_start", "plant_id", "ingredient_id", "kg_consumed", "available_at"),
    "T16_fact_purchase_orders": ("po_id", "supplier_id", "ingredient_id", "qty_kg", "order_date", "planned_eta", "available_at"),
    "T17_fact_air_freight": ("booking_id", "booking_date", "arrival_date", "ingredient_id", "kg", "rate_inr_per_kg", "cost_inr", "available_at"),
    "T18_fact_writeoff_discount": ("txn_date", "depot_id", "flavor_id", "type", "bottles", "cost_inr", "available_at"),
    "T19_fact_transfer_log": ("transfer_id", "from_depot", "to_depot", "flavor_id", "qty_bottles", "dispatch_date", "arrival_date", "cost_inr", "available_at"),
    "T20_log_decisions": ("decision_id", "alert_id", "action_type", "flavor_id", "decision", "approver_role", "decided_at"),
    "X01_fact_route_status_daily": ("date", "route_id", "supplier_id", "status", "closure_day_n", "known_at"),
}


@dataclass
class SchemaReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    row_counts: dict[str, tuple[int, int | None]] = field(default_factory=dict)   # sheet -> (actual, expected)

    def raise_if_failed(self) -> None:
        if self.errors:
            raise SchemaError("; ".join(self.errors))


def parse_table_index(idx: pd.DataFrame) -> dict[str, dict]:
    """Map sheet name (ID_table) -> {role, rows, columns[]} from the Table_Index sheet."""
    out: dict[str, dict] = {}
    for _, r in idx.iterrows():
        sheet = f"{r['ID']}_{r['table']}"
        cols = [c.strip() for c in str(r["columns"]).split(",") if c.strip()]
        out[sheet] = {"role": str(r["role"]), "rows": int(r["rows"]), "columns": cols}
    return out


def validate_workbook(sheets: dict[str, pd.DataFrame]) -> SchemaReport:
    rep = SchemaReport()
    for name in DOC_SHEETS + MASTER_SHEETS + REFERENCE_SHEETS + INPUT_FACT_SHEETS + TRUTH_SHEETS:
        if name not in sheets:
            rep.errors.append(f"missing sheet '{name}'")
    if rep.errors:
        rep.ok = False
        return rep
    index = parse_table_index(sheets["Table_Index"])
    for sheet, need in MANDATORY_COLUMNS.items():
        missing = [c for c in need if c not in sheets[sheet].columns]
        if missing:
            rep.errors.append(f"sheet '{sheet}' is missing mandatory column(s): {', '.join(missing)}")
    for sheet, meta in index.items():
        if sheet not in sheets:
            continue
        df = sheets[sheet]
        rep.row_counts[sheet] = (len(df), meta["rows"])
        absent = [c for c in meta["columns"] if c not in df.columns]
        if absent and sheet not in MANDATORY_COLUMNS:
            rep.warnings.append(f"sheet '{sheet}' lacks optional column(s): {', '.join(absent)}")
        elif absent:
            rep.warnings.append(f"sheet '{sheet}' lacks Table_Index column(s): {', '.join(absent)}")
        if len(df) != meta["rows"]:
            rep.warnings.append(f"sheet '{sheet}' has {len(df)} rows, Table_Index says {meta['rows']}")
    rep.ok = not rep.errors
    return rep
