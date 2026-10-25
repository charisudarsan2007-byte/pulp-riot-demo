# Pulp Riot supply chain agent

Working prototype for PS1 (The Dragon Passion Problem). Demand and inventory are the two deep modules; supply, distribution and transport are covered at a high level.
Stack: FastAPI + pydantic (backend), React + Tailwind + Plotly (front end), SQLite (decisions), pytest. Design: National Design Studio base tokens.

## Run
1. `run.bat` (installs, builds the front end, serves on http://localhost:8000). First start reads the workbook (~10 s, then cached).
2. Dev: `cd backend && python -m uvicorn pulpriot.api.main:app --port 8000` and `cd frontend && npm run dev` (port 5173, proxies /api).
3. Tests: `cd backend && python -m pytest` (needs `Pulp_Riot_Synthetic_Dataset.xlsx` in this folder, or set `PULPRIOT_WORKBOOK`).

## How it is built
- `backend/pulpriot/data`: workbook loader, schema checks from Table_Index, dirty-row cleaning with audit, arrival-time snapshots. Truth sheets and hindsight columns are stripped; only `eval/replay.py` and `api/views_replay.py` import `data/truth.py` (enforced by a test).
- `engine/`: spec v1.1 modules (censoring, monthly baseline, daily QC filter, CUSUM event detector with scenario mixture, depot depletion, temporal combine, launch cold start, hierarchy and path aggregation, uncertainty, lost demand) and the function contract in `forecast.py`.
- `inventory/`, `supply/`, `finance/`, `actions/`, `agent/`: nowcast, cover, safety stock, shelf life and FEFO, transfers, mitigation options, pulp, capacity, waterfall, approval register with audit log, rule-based Ask-the-Agent (no LLM).
- `frontend/src/pages`: Control tower, Demand, Stock, Supply, Money at stake, Decisions, Replay.

## Departures from the spec, all deliberate
- The QC feed has no `orders_on_company`: detection uses units sold and skips stockout days (tag UNITS_SOLD_SIGNAL).
- Noise floor 0.08, a 10-day month-boundary guard, a 1.2x event gate, week-constant launch template and month-constant seasonality: tuned on this feed because its volumes step at month boundaries (injection test in the app).
- Depot-level forecasts are never fixed fractions; pass-through from QC to GT/MT stays 0 (ASSUMED) until 8 uncensored overlapping weeks exist, which this data does not provide for Dragon Passion.
- Approval value limits and the 24-hour escalation time are ASSUMED (the workbook gives no figures).
