"""Synthetic truth and labels. VALIDATION ONLY.

Imported by eval/ (replay, scorecard, backtest scoring) and by the API's replay routes. It must never be
imported by engine/, inventory/, supply/, finance/, actions/ or agent/. tests/test_truth_isolation.py enforces
that with an import scan."""
from __future__ import annotations

import pandas as pd

from .loader import read_raw_sheets
from .schema import HINDSIGHT_COLUMNS


class Truth:
    def __init__(self) -> None:
        self._sheets, _ = read_raw_sheets()

    def sheet(self, name: str) -> pd.DataFrame:
        return self._sheets[name].copy()

    def demand_daily(self) -> pd.DataFrame:
        d = self.sheet("V01_truth_demand_daily")
        d["date"] = pd.to_datetime(d["date"])
        return d

    def stock_daily(self) -> pd.DataFrame:
        d = self.sheet("V02_truth_stock_daily")
        d["date"] = pd.to_datetime(d["date"])
        return d

    def qc_true(self) -> pd.DataFrame:
        """T02's hindsight column, re-read for scoring only."""
        d = self.sheet("T02_fact_qc_daily")[["date", "flavor_id", "depot_id", "region_id", "units_sold", "demand_true", "stockout_flag"]]
        d["date"] = pd.to_datetime(d["date"])
        return d

    def labels_daily(self) -> pd.DataFrame:
        d = self.sheet("L01_label_daily")
        d["date"] = pd.to_datetime(d["date"])
        return d

    def labels_supply(self) -> pd.DataFrame:
        d = self.sheet("L02_label_daily_supply")
        d["date"] = pd.to_datetime(d["date"])
        return d

    def label_events(self) -> pd.DataFrame:
        return self.sheet("L03_label_events")

    def flag_truth(self, scenario: str = "SC03_LAST_SUMMER") -> pd.DataFrame:
        d = self.sheet("Scenario_Flag_Truth")
        return d[d["scenario_id"] == scenario].reset_index(drop=True)

    def scenario_catalog(self) -> pd.DataFrame:
        return self.sheet("Scenario_Catalog")

    def forecast_actuals_adjusted(self) -> pd.DataFrame:
        d = self.sheet("T11_fact_forecast_history")
        return d[["forecast_made_month", "target_month", "flavor_id", "depot_id", "actual_demand_est", "abs_pct_error"]]


HINDSIGHT = HINDSIGHT_COLUMNS
