from pathlib import Path
from unittest.mock import MagicMock, patch

from nas_air_intelligence.analysis import WhisperSpeechAnalyzer, analyze_pending_chunks
from nas_air_intelligence.db import Database
from nas_air_intelligence.transcription import (
    TranscriptionResult,
    TranscriptionSegment,
)


def test_whisper_speech_analyzer_events():
    mock_transcriber = MagicMock()
    mock_res = TranscriptionResult(
        text="إذاعة الناس اف ام",
        language="ar",
        language_probability=0.99,
        duration=10.0,
        processing_time=0.5,
        segments=[
            TranscriptionSegment(
                start=2.0,
                end=8.0,
                text="إذاعة الناس اف ام",
                avg_logprob=-0.2,
                no_speech_prob=0.01,
                confidence=0.82,
            )
        ],
        model="tiny",
        device="cuda",
        compute_type="float16",
    )
    mock_transcriber.transcribe_safe.return_value = (mock_res, None)

    analyzer = WhisperSpeechAnalyzer(
        transcriber=mock_transcriber,
        enable_fingerprinting=False,
    )

    with patch(
        "nas_air_intelligence.analysis.silence_intervals",
        return_value=[("audio", 0.0, 8.0), ("silence", 8.0, 10.0)],
    ):
        events = analyzer.analyze(Path("dummy.mp3"), 10.0)

    kinds = [e["kind"] for e in events]
    assert "speech" in kinds
    assert "silence" in kinds

    speech_event = next(e for e in events if e["kind"] == "speech")
    assert speech_event["text"] == "إذاعة الناس اف ام"
    assert speech_event["start_offset"] == 2.0
    assert speech_event["end_offset"] == 8.0
    assert speech_event["confidence"] == 0.82
    assert speech_event["metadata"]["language"] == "ar"


def test_whisper_speech_analyzer_transcription_failure():
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe_safe.return_value = (None, "Decode error in PyAV")

    analyzer = WhisperSpeechAnalyzer(
        transcriber=mock_transcriber,
        enable_fingerprinting=False,
    )

    with patch("nas_air_intelligence.analysis.silence_intervals", return_value=[]):
        events = analyzer.analyze(Path("dummy.mp3"), 10.0)

    assert len(events) == 1
    assert events[0]["kind"] == "unknown"
    assert events[0]["label"] == "transcription-failure"
    assert events[0]["metadata"]["error"] == "Decode error in PyAV"
    assert events[0]["metadata"]["status"] == "incident"


def test_analyze_pending_chunks_records_incidents_without_crash(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    station_id = db.upsert_station("Station A", "https://stream.invalid")
    session_id = db.create_session(station_id, 300, 30, str(tmp_path))
    chunk_file = tmp_path / "chunk1.mp3"
    chunk_file.write_bytes(b"dummy")

    db.add_chunk(
        session_id=session_id,
        path=str(chunk_file),
        started_at="2026-10-05T12:00:00Z",
        ended_at="2026-10-05T12:00:30Z",
        duration_seconds=30.0,
        sha256="dummyhash",
    )

    failing_analyzer = MagicMock()
    failing_analyzer.analyze.side_effect = RuntimeError("Simulated crash")

    count = analyze_pending_chunks(db, session_id, failing_analyzer)
    assert count == 1  # Chunk was processed and marked analyzed

    # Verify incident was logged
    incidents = db.incidents(session_id)
    assert len(incidents) == 1
    assert incidents[0]["kind"] == "analysis_failure"
    assert "Simulated crash" in incidents[0]["details"]

    # Verify event was stored with kind unknown
    events = db.events(session_id)
    assert len(events) == 1
    assert events[0]["kind"] == "unknown"
    assert events[0]["label"] == "analyzer-crashed"
