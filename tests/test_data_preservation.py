"""Tests verifying data preservation, isolated storage paths, and SQLite upgrade safety."""
from __future__ import annotations

from pathlib import Path

from nas_air_intelligence.db import Database
from nas_air_intelligence.sidecar import default_db_path, default_storage_dir


def test_storage_dir_isolation_from_nsis_install(monkeypatch) -> None:
    """Storage directory must be distinct from standard NSIS app install directories."""
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\testuser\AppData\Local")
    monkeypatch.delenv("NAS_AIR_STORAGE", raising=False)

    storage = default_storage_dir()
    expected = Path(r"C:\Users\testuser\AppData\Local\NAS Air Intelligence Data")
    assert storage == expected

    # NSIS standard install path for per-user installs:
    # C:\Users\testuser\AppData\Local\Programs\NAS Air Intelligence
    # or C:\Users\testuser\AppData\Local\NAS Air Intelligence
    nsis_programs = Path(r"C:\Users\testuser\AppData\Local\Programs\NAS Air Intelligence")
    nsis_direct = Path(r"C:\Users\testuser\AppData\Local\NAS Air Intelligence")

    assert storage != nsis_programs
    assert storage != nsis_direct
    assert not storage.is_relative_to(nsis_programs)
    assert not storage.is_relative_to(nsis_direct)


def test_database_upgrade_and_data_preservation(tmp_path: Path) -> None:
    """A database created by an older version must keep all rows and schema intact."""
    storage_dir = tmp_path / "NAS Air Intelligence Data"
    storage_dir.mkdir(parents=True)
    db_file = default_db_path(storage_dir)

    # 1. Seed database as vOld
    old_db = Database(db_file)
    old_db.initialize()

    station_id = old_db.upsert_station("NAS FM 98.7", "https://example.com/stream.mp3")
    session_id = old_db.create_session(
        station_id=station_id,
        target_seconds=600.0,
        segment_seconds=300,
        output_dir=str(storage_dir / "sessions" / "session-1"),
    )

    chunk_path = str(storage_dir / "sessions" / "session-1" / "chunk-000001.mp3")
    old_db.add_chunk(
        session_id=session_id,
        path=chunk_path,
        started_at="2026-10-08T18:00:00+00:00",
        ended_at="2026-10-08T18:05:00+00:00",
        duration_seconds=300.0,
        sha256="abcd1234efgh5678",
    )

    old_db.add_events(
        [
            {
                "session_id": session_id,
                "kind": "speech",
                "start_offset": 10.0,
                "end_offset": 50.0,
                "confidence": 0.95,
                "label": "Arabic Speech",
                "text": "مرحبا بكم في إذاعة ناس إف إم",
            }
        ]
    )

    old_db.finish_session(session_id, "completed")

    # 2. Simulate upgrade: New version opens the existing database and re-initializes
    new_db = Database(db_file)
    new_db.initialize()  # Must be idempotent

    # 3. Assert all previous records are fully preserved
    with new_db.connect() as conn:
        stations = conn.execute("SELECT * FROM stations").fetchall()
        assert len(stations) == 1
        assert stations[0]["name"] == "NAS FM 98.7"

        sessions = conn.execute("SELECT * FROM sessions").fetchall()
        assert len(sessions) == 1
        assert sessions[0]["id"] == session_id
        assert sessions[0]["status"] == "completed"

        chunks = conn.execute("SELECT * FROM chunks WHERE session_id = ?", (session_id,)).fetchall()
        assert len(chunks) == 1
        assert chunks[0]["path"] == chunk_path

        events = conn.execute("SELECT * FROM events WHERE session_id = ?", (session_id,)).fetchall()
        assert len(events) == 1
        assert events[0]["kind"] == "speech"
        assert events[0]["text"] == "مرحبا بكم في إذاعة ناس إف إم"

    # 4. Assert new version can continue adding records without conflict
    new_session_id = new_db.create_session(
        station_id=station_id,
        target_seconds=300.0,
        segment_seconds=150,
        output_dir=str(storage_dir / "sessions" / "session-2"),
    )
    assert new_session_id != session_id
    with new_db.connect() as conn:
        sessions = conn.execute("SELECT * FROM sessions").fetchall()
        assert len(sessions) == 2
