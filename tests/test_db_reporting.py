from pathlib import Path

from nas_air_intelligence.db import Database
from nas_air_intelligence.reporting import build_report, report_markdown


def test_database_and_report_roundtrip(tmp_path: Path):
    db = Database(tmp_path / "air.db")
    station_id = db.upsert_station("Test Station", "https://example.invalid/live")
    session_id = db.create_session(station_id, 100.0, 30, str(tmp_path / "audio"))
    chunk_id = db.add_chunk(
        session_id=session_id,
        path=str(tmp_path / "audio" / "chunk.mp3"),
        started_at="2026-10-05T10:00:00Z",
        ended_at="2026-10-05T10:00:30Z",
        duration_seconds=30.0,
        sha256="abc",
    )
    assert chunk_id
    db.add_events(
        [
            {
                "session_id": session_id,
                "chunk_id": chunk_id,
                "kind": "audio",
                "start_offset": 0.0,
                "end_offset": 25.0,
            },
            {
                "session_id": session_id,
                "chunk_id": chunk_id,
                "kind": "silence",
                "start_offset": 25.0,
                "end_offset": 30.0,
            },
        ]
    )
    db.mark_chunk_analyzed(chunk_id)
    db.finish_session(session_id, "completed")

    report = build_report(db, session_id)
    assert report["monitoring"]["chunk_count"] == 1
    assert report["monitoring"]["coverage_percent_of_target"] == 30.0
    assert {item["kind"] for item in report["analysis"]["distribution"]} == {
        "audio",
        "silence",
    }
    markdown = report_markdown(report)
    assert "Test Station" in markdown
    assert "audio" in markdown
