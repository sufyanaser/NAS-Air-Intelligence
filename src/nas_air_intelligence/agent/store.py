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
