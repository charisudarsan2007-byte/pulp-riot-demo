"""Action register and audit log (SQLite).

Every recommendation starts PENDING. Approve, reject and modify save the human's decision and its reason; nothing is executed.
Persistence is a local SQLite file (prototype identity, no login): the actor is whoever the "Acting as" role selector says.
Each decision writes an audit row with actor, role, the data snapshot the recommendation rested on, and the quantities."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..settings import db_path

_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
  id TEXT PRIMARY KEY,
  created_asof TEXT NOT NULL,
  updated_asof TEXT NOT NULL,
  type TEXT NOT NULL,
  flavor TEXT,
  depot TEXT,
  qty REAL,
  unit TEXT,
  title TEXT NOT NULL,
  rationale TEXT,
  source_snapshot TEXT,
  exposure_inr REAL,
  cost_inr REAL,
  net_benefit_inr REAL,
  deadline TEXT,
  approver_role TEXT NOT NULL,
  group_key TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING',
  decided_asof TEXT,
  decided_by TEXT,
  decision_reason TEXT,
  approved_qty REAL,
  model_version TEXT,
  config_hash TEXT,
  payload TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action_id TEXT NOT NULL,
  wall_ts TEXT NOT NULL,
  asof TEXT,
  actor_role TEXT,
  event TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  qty REAL,
  reason TEXT,
  snapshot TEXT
);
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  start_date TEXT NOT NULL,
  end_date TEXT,
  flavor TEXT,
  kind TEXT NOT NULL,
  note TEXT,
  expected_uplift REAL,
  entered_by TEXT,
  entered_asof TEXT
);
"""


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_recommendation(rec: dict, asof: str) -> dict:
    """Insert a new PENDING recommendation or refresh a still-pending one. Decided rows keep their saved snapshot."""
    with _LOCK, conn() as c:
        row = c.execute("SELECT * FROM actions WHERE id=?", (rec["id"],)).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO actions (id, created_asof, updated_asof, type, flavor, depot, qty, unit, title, rationale, source_snapshot,"
                " exposure_inr, cost_inr, net_benefit_inr, deadline, approver_role, group_key, status, model_version, config_hash, payload)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'PENDING', ?,?,?)",
                (rec["id"], asof, asof, rec["type"], rec.get("flavor"), rec.get("depot"), rec.get("qty"), rec.get("unit", "bottles"), rec["title"],
                 rec.get("rationale"), json.dumps(rec.get("source_snapshot", {}), default=str), rec.get("exposure_inr"), rec.get("cost_inr"),
                 rec.get("net_benefit_inr"), rec.get("deadline"), rec["approver_role"], rec.get("group_key"), rec.get("model_version"),
                 rec.get("config_hash"), json.dumps(rec.get("payload", {}), default=str)))
            c.execute("INSERT INTO audit_log (action_id, wall_ts, asof, actor_role, event, from_status, to_status, qty, reason, snapshot)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (rec["id"], _now(), asof, "ENGINE", "CREATED", None, "PENDING", rec.get("qty"), rec["title"],
                       json.dumps(rec.get("source_snapshot", {}), default=str)))
        elif row["status"] == "PENDING":
            c.execute("UPDATE actions SET updated_asof=?, qty=?, title=?, rationale=?, source_snapshot=?, exposure_inr=?, cost_inr=?,"
                      " net_benefit_inr=?, deadline=?, approver_role=?, payload=? WHERE id=?",
                      (asof, rec.get("qty"), rec["title"], rec.get("rationale"), json.dumps(rec.get("source_snapshot", {}), default=str),
                       rec.get("exposure_inr"), rec.get("cost_inr"), rec.get("net_benefit_inr"), rec.get("deadline"), rec["approver_role"],
                       json.dumps(rec.get("payload", {}), default=str), rec["id"]))
        out = c.execute("SELECT * FROM actions WHERE id=?", (rec["id"],)).fetchone()
        return dict(out)


def get_action(action_id: str) -> dict | None:
    with conn() as c:
        r = c.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        return dict(r) if r else None


def list_actions(asof: str | None = None) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT * FROM actions ORDER BY created_asof, id").fetchall()
    out = [dict(r) for r in rows]
    if asof:
        out = [r for r in out if r["created_asof"] <= asof]
    return out


def decide(action_id: str, decision: str, actor_role: str, asof: str, reason: str | None, approved_qty: float | None) -> dict:
    """Record approve / reject / modify. The caller has already checked the role gate."""
    status = {"approve": "APPROVED", "reject": "REJECTED", "modify": "MODIFIED"}[decision]
    with _LOCK, conn() as c:
        row = c.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            raise KeyError(action_id)
        prev = row["status"]
        qty = approved_qty if decision in ("modify",) else (row["qty"] if decision == "approve" else 0.0)
        c.execute("UPDATE actions SET status=?, decided_asof=?, decided_by=?, decision_reason=?, approved_qty=? WHERE id=?",
                  (status, asof, actor_role, reason, qty, action_id))
        c.execute("INSERT INTO audit_log (action_id, wall_ts, asof, actor_role, event, from_status, to_status, qty, reason, snapshot)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (action_id, _now(), asof, actor_role, decision.upper(), prev, status, qty, reason, row["source_snapshot"]))
        return dict(c.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())


def audit(action_id: str | None = None) -> list[dict]:
    with conn() as c:
        if action_id:
            rows = c.execute("SELECT * FROM audit_log WHERE action_id=? ORDER BY id", (action_id,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


def reset_all() -> None:
    """Demo reset (used by tests and the 'reset demo decisions' control)."""
    with _LOCK, conn() as c:
        c.execute("DELETE FROM actions")
        c.execute("DELETE FROM audit_log")
        c.execute("DELETE FROM campaigns")


# ---- campaigns (marketing calendar entries typed in by planners) ------------------------------------------------
def add_campaign(c_: dict) -> dict:
    with _LOCK, conn() as c:
        cur = c.execute("INSERT INTO campaigns (start_date, end_date, flavor, kind, note, expected_uplift, entered_by, entered_asof) VALUES (?,?,?,?,?,?,?,?)",
                        (c_["start_date"], c_.get("end_date"), c_.get("flavor"), c_["kind"], c_.get("note"), c_.get("expected_uplift"),
                         c_.get("entered_by"), c_.get("entered_asof")))
        return dict(c.execute("SELECT * FROM campaigns WHERE id=?", (cur.lastrowid,)).fetchone())


def list_campaigns() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM campaigns ORDER BY start_date").fetchall()]
