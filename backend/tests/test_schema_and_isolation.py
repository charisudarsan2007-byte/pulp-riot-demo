"""Workbook loader, schema validation, source tags and the truth-isolation boundary."""
import ast
import copy
from pathlib import Path

import pandas as pd
import pytest

from pulpriot.data import schema as S
from pulpriot.data.loader import build_dataset, read_raw_sheets

PKG = Path(__file__).resolve().parents[1] / "pulpriot"
ENGINE_SIDE = ["engine", "inventory", "supply", "finance", "actions", "agent"]


def test_every_expected_sheet_present(ds):
    sheets, _ = read_raw_sheets()
    for name in S.DOC_SHEETS + S.MASTER_SHEETS + S.REFERENCE_SHEETS + S.INPUT_FACT_SHEETS + S.TRUTH_SHEETS:
        assert name in sheets, name


def test_row_counts_match_table_index(ds):
    for sheet, (actual, expected) in ds.schema_report.row_counts.items():
        if sheet.startswith(("M14",)):
            continue
        assert actual == expected, f"{sheet}: {actual} rows vs Table_Index {expected}"


def test_missing_mandatory_column_fails_helpfully():
    sheets, sig = read_raw_sheets()
    bad = copy.copy(sheets)
    bad["T04_fact_depot_stock_weekly"] = sheets["T04_fact_depot_stock_weekly"].drop(columns=["report_received_at"])
    with pytest.raises(S.SchemaError) as e:
        build_dataset(bad, sig)
    assert "T04_fact_depot_stock_weekly" in str(e.value) and "report_received_at" in str(e.value)


def test_missing_sheet_fails_helpfully():
    sheets, sig = read_raw_sheets()
    bad = {k: v for k, v in sheets.items() if k != "T02_fact_qc_daily"}
    with pytest.raises(S.SchemaError) as e:
        build_dataset(bad, sig)
    assert "T02_fact_qc_daily" in str(e.value)


def test_truth_sheets_and_hindsight_columns_not_in_inputs(ds):
    for short, df in ds.facts.items():
        assert "demand_true" not in df.columns, short
        assert "status_as_of_2026_08_31" not in df.columns
        assert "actual_demand_est" not in df.columns
    po = ds.facts["purchase_orders"]
    assert "delay_days" not in po.columns and "status" not in po.columns and "eta" not in po.columns


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out.add(("." * n.level) + (n.module or ""))
            out |= {(n.module or "") + "." + a.name for a in n.names}
    return out


def test_engine_side_packages_never_import_truth_or_eval():
    offenders = []
    for pkg in ENGINE_SIDE:
        for f in (PKG / pkg).rglob("*.py"):
            for imp in _imports(f):
                if "truth" in imp.split(".")[-1] or imp.endswith("data.truth") or ".eval" in imp or imp.startswith("pulpriot.eval"):
                    offenders.append((str(f.relative_to(PKG)), imp))
    assert not offenders, offenders


def test_truth_is_only_imported_by_evaluation_code():
    importers = set()
    for f in PKG.rglob("*.py"):
        if any(i.split(".")[-1] == "truth" or i.endswith(".truth.Truth") or i.endswith("truth.Truth") for i in _imports(f)):
            importers.add(str(f.relative_to(PKG)).replace("\\", "/"))
    assert importers <= {"eval/replay.py", "api/views_replay.py"}, importers


def test_arrival_columns_cover_every_input_table(ds):
    for short, col in ds.arrival.items():
        assert col in ds.facts[short].columns, (short, col)


def test_provenance_data_tags_survive_loading(ds):
    for short in ("sales_monthly", "qc_daily", "stock_weekly"):
        assert "data_tag" in ds.facts[short].columns
        assert ds.facts[short]["data_tag"].astype(str).str.contains("SYNTH").all()


def test_dirty_fixture_rows_are_all_handled(svc):
    fx = svc.trust("2026-03-24")["fixture"]
    assert fx["total"] == 40 and fx["handled"] == 40
    bad = [r for r in fx["rows"] if r["handled"] == "NOT HANDLED"]
    assert not bad


def test_dirty_fixture_actions_match_expected(svc):
    rows = {r["row_id"]: r for r in svc.trust("2026-03-24")["fixture"]["rows"]}
    kinds = {(r["target"], r["defect"]): r["handled"] for r in rows.values()}
    assert "REJECT" in kinds[("fact_qc_daily", "bad_date")]
    assert "REJECT" in kinds[("fact_qc_daily", "negative")]
    assert "FLAG" in kinds[("fact_qc_daily", "unit_error")]
    assert "REJECT" in kinds[("fact_depot_stock_weekly", "unknown_id")]
    assert "QUARANTINE" in kinds[("fact_depot_stock_weekly", "negative")]
    assert "MAPPED" in kinds[("fact_depot_stock_weekly", "bad_name")]


def test_clean_tables_have_no_silent_drops(ds):
    sheets, _ = read_raw_sheets()
    assert len(ds.facts["qc_daily"]) + len(ds.audit[ds.audit.table == "T02_fact_qc_daily"]) == len(sheets["T02_fact_qc_daily"])
    assert len(ds.facts["stock_weekly"]) + len(ds.audit[ds.audit.table == "T04_fact_depot_stock_weekly"]) == len(sheets["T04_fact_depot_stock_weekly"])
