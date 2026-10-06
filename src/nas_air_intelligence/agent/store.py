from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from ..db import Database
from ..util import isoformat, parse_iso, utc_now
from .states import TERMINAL_STATES, AgentState, check_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    station_name TEXT NOT NULL,
    source_page TEXT,
    requested_url TEXT,
    resolved_json TEXT,
    mode TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    segment_seconds INTEGER NOT NULL,
    analyzer TEXT NOT NULL,
    model TEXT,
    state TEXT NOT NULL,
    session_id TEXT,
    pid INTEGER,
    heartbeat_at TEXT,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_created ON agent_runs(created_at);

-- Agent Run Journal: structured events the desktop activity feed reads. Never inferred by
-- parsing stdout/stderr - every row here is written by the orchestrator at a known stage.
CREATE TABLE IF NOT EXISTS agent_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    session_id TEXT,
    occurred_at TEXT NOT NULL,
    stage TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    severity TEXT NOT NULL DEFAULT 'info'
);
CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_events(run_id, occurred_at);
"""

HEARTBEAT_STALE_SECONDS = 45.0
_JSON_COLUMNS = {"resolved_json": "resolved", "warnings_json": "warnings", "result_json": "result"}
_TERMINAL_VALUES = {s.value for s in TERMINAL_STATES}


class AgentStore:
    """Agent run persistence. Lives in the same SQLite file as the monitoring core."""

    def __init__(self, db: Database) -> None:
        self.db = db
        db.initialize()
        with db.connect() as conn:
            conn.executescript(SCHEMA)

    def create(
        self,
        *,
        station_name: str,
        source_page: str | None,
        requested_url: str | None,
        mode: str,
        duration_seconds: float,
        segment_seconds: int,
        analyzer: str,
        model: str | None,
    ) -> str:
        run_id = str(uuid.uuid4())
        now = isoformat(utc_now())
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO agent_runs(
                    id, station_name, source_page, requested_url, mode, duration_seconds,
                    segment_seconds, analyzer, model, state, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    station_name,
                    source_page,
                    requested_url,
                    mode,
                    duration_seconds,
                    segment_seconds,
                    analyzer,
                    model,
                    AgentState.CREATED.value,
                    now,
                    now,
                ),
            )
        return run_id

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM agent_runs WHERE id=?", (run_id,))
        return self._decode(row) if row else None

    def by_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM agent_runs WHERE session_id=?", (session_id,))
        return self._decode(row) if row else None

    def all(self) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT * FROM agent_runs ORDER BY created_at DESC")
        return [self._decode(r) for r in rows]

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for column, name in _JSON_COLUMNS.items():
            raw = row.pop(column, None)
            row[name] = json.loads(raw) if raw else ([] if name == "warnings" else None)
        row["is_terminal"] = row["state"] in _TERMINAL_VALUES
        row["worker_alive"] = AgentStore.worker_alive(row)
        return row

    @staticmethod
    def worker_alive(row: dict[str, Any]) -> bool:
        if row["state"] in _TERMINAL_VALUES or not row.get("heartbeat_at"):
            return False
        age = utc_now() - parse_iso(row["heartbeat_at"])
        return age < timedelta(seconds=HEARTBEAT_STALE_SECONDS)

    def update(self, run_id: str, **fields: Any) -> None:
        sets, params = [], []
        for key, value in fields.items():
            if key in {"resolved", "warnings", "result"}:
                key = f"{key}_json"
                value = json.dumps(value, ensure_ascii=False)
            sets.append(f"{key}=?")
            params.append(value)
        sets.append("updated_at=?")
        params.append(isoformat(utc_now()))
        params.append(run_id)
        with self.db.connect() as conn:
            conn.execute(f"UPDATE agent_runs SET {', '.join(sets)} WHERE id=?", params)

    def transition(self, run_id: str, new: AgentState, **fields: Any) -> None:
        current = self.get(run_id)
        if current is None:
            raise KeyError(f"unknown agent run: {run_id}")
        check_transition(AgentState(current["state"]), new)
        self.update(run_id, state=new.value, **fields)

    def heartbeat(self, run_id: str) -> None:
        self.update(run_id, heartbeat_at=isoformat(utc_now()))

    def request_stop(self, run_id: str) -> None:
        self.update(run_id, stop_requested=1)

    def stop_requested(self, run_id: str) -> bool:
        row = self.db.one("SELECT stop_requested FROM agent_runs WHERE id=?", (run_id,))
        return bool(row and row["stop_requested"])

    def add_warning(self, run_id: str, message: str) -> None:
        row = self.get(run_id)
        warnings = row["warnings"] if row else []
        if message not in warnings:
            warnings.append(message)
            self.update(run_id, warnings=warnings)

    # ------------------------------------------------------------------ run journal
    def log_event(
        self,
        run_id: str,
        *,
        stage: str,
        event_type: str,
        status: str = "ok",
        message: str | None = None,
        details: dict[str, Any] | None = None,
        severity: str = "info",
        session_id: str | None = None,
    ) -> None:
        """Append one Agent Run Journal row. The desktop activity feed reads these back."""
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO agent_events(
                    id, run_id, session_id, occurred_at, stage, event_type,
                    status, message, details_json, severity
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()),
                    run_id,
                    session_id,
                    isoformat(utc_now()),
                    stage,
                    event_type,
                    status,
                    message,
                    json.dumps(details or {}, ensure_ascii=False),
                    severity,
                ),
            )

    def events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        """Journal rows, newest first - exactly what an activity feed renders."""
        rows = self.db.all(
            "SELECT * FROM agent_events WHERE run_id=? ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (run_id, limit),
        )
        for row in rows:
            row["details"] = json.loads(row.pop("details_json") or "{}")
        return rows

    def find(self, ident: str) -> dict[str, Any] | None:
        """Look up a run by id, by its session id, or by an unambiguous id prefix."""
        run = self.get(ident) or self.by_session(ident)
        if run:
            return run
        matches = [r for r in self.all() if r["id"].startswith(ident)]
        return matches[0] if len(matches) == 1 else None
