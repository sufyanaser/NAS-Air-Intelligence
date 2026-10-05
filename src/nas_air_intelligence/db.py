from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .util import isoformat, utc_now

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS stations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    stream_url TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Baghdad',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(id),
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    target_seconds REAL NOT NULL,
    segment_seconds INTEGER NOT NULL,
    output_dir TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    sha256 TEXT NOT NULL,
    analyzed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    chunk_id TEXT REFERENCES chunks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    start_offset REAL NOT NULL,
    end_offset REAL NOT NULL,
    confidence REAL,
    label TEXT,
    text TEXT,
    fingerprint TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    occurred_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_station ON sessions(station_id, started_at);
CREATE INDEX IF NOT EXISTS idx_chunks_session ON chunks(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, kind);
CREATE INDEX IF NOT EXISTS idx_incidents_session ON incidents(session_id, occurred_at);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @staticmethod
    def _id() -> str:
        return str(uuid.uuid4())

    def upsert_station(self, name: str, stream_url: str, timezone: str = "Asia/Baghdad") -> str:
        self.initialize()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM stations WHERE name = ? AND stream_url = ?",
                (name, stream_url),
            ).fetchone()
            if row:
                return str(row["id"])
            station_id = self._id()
            conn.execute(
                """INSERT INTO stations(
                    id, name, stream_url, timezone, created_at
                ) VALUES(?,?,?,?,?)""",
                (station_id, name, stream_url, timezone, isoformat(utc_now())),
            )
            return station_id

    def create_session(
        self,
        station_id: str,
        target_seconds: float,
        segment_seconds: int,
        output_dir: str,
    ) -> str:
        self.initialize()
        session_id = self._id()
        now = isoformat(utc_now())
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sessions(
                    id, station_id, status, started_at, target_seconds,
                    segment_seconds, output_dir, created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    session_id,
                    station_id,
                    "running",
                    now,
                    target_seconds,
                    segment_seconds,
                    output_dir,
                    now,
                ),
            )
        return session_id

    def finish_session(self, session_id: str, status: str, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET status=?, ended_at=?, error=? WHERE id=?",
                (status, isoformat(utc_now()), error, session_id),
            )

    def add_chunk(
        self,
        session_id: str,
        path: str,
        started_at: str,
        ended_at: str,
        duration_seconds: float,
        sha256: str,
    ) -> str | None:
        chunk_id = self._id()
        try:
            with self.connect() as conn:
                conn.execute(
                    """INSERT INTO chunks(
                        id, session_id, path, started_at, ended_at,
                        duration_seconds, sha256, created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        chunk_id,
                        session_id,
                        path,
                        started_at,
                        ended_at,
                        duration_seconds,
                        sha256,
                        isoformat(utc_now()),
                    ),
                )
        except sqlite3.IntegrityError:
            return None
        return chunk_id

    def mark_chunk_analyzed(self, chunk_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE chunks SET analyzed=1 WHERE id=?", (chunk_id,))

    def add_events(self, events: Iterable[dict[str, Any]]) -> None:
        rows = []
        for event in events:
            rows.append(
                (
                    event.get("id", self._id()),
                    event["session_id"],
                    event.get("chunk_id"),
                    event["kind"],
                    float(event["start_offset"]),
                    float(event["end_offset"]),
                    event.get("confidence"),
                    event.get("label"),
                    event.get("text"),
                    event.get("fingerprint"),
                    json.dumps(event.get("metadata", {}), ensure_ascii=False),
                    isoformat(utc_now()),
                )
            )
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO events(
                    id, session_id, chunk_id, kind, start_offset, end_offset,
                    confidence, label, text, fingerprint, metadata_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def add_incident(self, session_id: str, kind: str, details: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO incidents(
                    id, session_id, occurred_at, kind, details, created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    self._id(),
                    session_id,
                    isoformat(utc_now()),
                    kind,
                    details,
                    isoformat(utc_now()),
                ),
            )

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def station(self, station_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM stations WHERE id=?", (station_id,))

    def stations(self) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM stations ORDER BY created_at DESC")

    def session(self, session_id: str) -> dict[str, Any] | None:
        return self.one(
            """SELECT s.*, st.name AS station_name, st.stream_url, st.timezone
               FROM sessions s JOIN stations st ON st.id=s.station_id
               WHERE s.id=?""",
            (session_id,),
        )

    def sessions(self) -> list[dict[str, Any]]:
        return self.all(
            """SELECT s.*, st.name AS station_name
               FROM sessions s JOIN stations st ON st.id=s.station_id
               ORDER BY s.started_at DESC"""
        )

    def chunks(self, session_id: str) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM chunks WHERE session_id=? ORDER BY started_at",
            (session_id,),
        )

    def unanalyzed_chunks(self, session_id: str) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM chunks WHERE session_id=? AND analyzed=0 ORDER BY started_at",
            (session_id,),
        )

    def events(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.all(
            "SELECT * FROM events WHERE session_id=? ORDER BY chunk_id, start_offset",
            (session_id,),
        )
        for row in rows:
            row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
        return rows

    def incidents(self, session_id: str) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM incidents WHERE session_id=? ORDER BY occurred_at",
            (session_id,),
        )
