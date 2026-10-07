"""Red Team & Analytical Validity Test Suite.

Proves that technical failures can NEVER become programming evidence.
Verifies all 8 failure fixtures, exact Al Nakhla 10-minute acceptance criteria,
sample sufficiency gates, preflight blocking, and healthy analytical execution.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nas_air_intelligence.agent.excel_export import write_workbook
from nas_air_intelligence.agent.preflight import (
    PreflightChecker,
    PreflightCheckStatus,
    PreflightError,
    PreflightMode,
    PreflightReasonCode,
    verify_preflight_or_raise,
)
from nas_air_intelligence.agent.programming import (
    build_programming_analysis,
)
from nas_air_intelligence.agent.report import (
    write_agent_report,
)
from nas_air_intelligence.agent.schema import validate_analysis_json
from nas_air_intelligence.agent.sufficiency import (
    assess_clock_sufficiency,
    assess_daypart_sufficiency,
)
from nas_air_intelligence.agent.timeline import (
    build_timeline,
    compute_timeline_coverage,
    station_timezone,
)
from nas_air_intelligence.agent.validity import (
    CaptureStatus,
    ClassificationStatus,
    DecisionReadiness,
    EvidenceClass,
    ProgrammingAnalysisStatus,
    SufficiencyStatus,
    TranscriptionStatus,
)
from nas_air_intelligence.db import Database
from nas_air_intelligence.util import isoformat

UTC_TZ = station_timezone("UTC")


def _setup_session(
    tmp_path: Path,
    *,
    station_name: str = "إذاعة النخلة (Al Nakhla FM)",
    chunk_count: int = 11,
    chunk_duration: float = 60.0,
    target_seconds: float = 600.0,
) -> tuple[Database, str]:
    db = Database(tmp_path / "validity.db")
    station_id = db.upsert_station(station_name, "https://stream.example/live.mp3")
    session_id = db.create_session(station_id, target_seconds, 60, str(tmp_path / "audio"))

    t0 = datetime(2026, 1, 5, 8, 0, 0, tzinfo=UTC_TZ)
    for i in range(chunk_count):
        start = t0 + timedelta(seconds=i * chunk_duration)
        end = start + timedelta(seconds=chunk_duration)
        db.add_chunk(
            session_id,
            f"/tmp/chunk_{i:03d}.mp3",
            isoformat(start),
            isoformat(end),
            chunk_duration,
            f"sha256_{i}",
        )
    return db, session_id


# ==============================================================================
# RED TEAM FIXTURE 1: faster-whisper missing
# ==============================================================================
def test_red_team_fixture_1_faster_whisper_missing(tmp_path: Path):
    """When faster-whisper is missing, preflight in CAPTURE_AND_ANALYSIS mode blocks start."""
    checker = PreflightChecker(storage_dir=tmp_path / "storage")

    with patch.dict("sys.modules", {"faster_whisper": None}):
        report = checker.run_preflight(
            mode=PreflightMode.CAPTURE_AND_ANALYSIS,
            station="Al Nakhla",
            url="https://live.mp3",
            analyzer="whisper",
        )
        assert report.blocked is True
        assert any("faster-whisper" in f for f in report.failures)
        assert PreflightReasonCode.TRANSCRIPTION_ENGINE_MISSING in report.reason_codes
        engine_check = next(c for c in report.checks if c.check_name == "transcription_engine")
        assert engine_check.status == PreflightCheckStatus.FAIL
        assert engine_check.reason_code == PreflightReasonCode.TRANSCRIPTION_ENGINE_MISSING

        with pytest.raises(PreflightError) as exc_info:
            verify_preflight_or_raise(
                checker,
                mode=PreflightMode.CAPTURE_AND_ANALYSIS,
                station="Al Nakhla",
                url="https://live.mp3",
            )
        assert "faster-whisper" in str(exc_info.value)


# ==============================================================================
# RED TEAM FIXTURE 2: model load failure
# ==============================================================================
def test_red_team_fixture_2_model_load_failure(tmp_path: Path):
    """Whisper model load crash blocks CAPTURE_AND_ANALYSIS start and marks validity as FAILED."""
    checker = PreflightChecker(storage_dir=tmp_path / "storage")

    mock_fw = MagicMock()
    with patch.dict("sys.modules", {"faster_whisper": mock_fw}), patch(
        "nas_air_intelligence.transcription.SpeechTranscriber._load_model",
        side_effect=RuntimeError("CUDA out of memory / model load failed"),
    ):
        report = checker.run_preflight(
            mode=PreflightMode.CAPTURE_AND_ANALYSIS,
            analyzer="whisper",
        )
        assert report.blocked is True
        assert PreflightReasonCode.MODEL_LOAD_FAILED in report.reason_codes
        model_check = next(c for c in report.checks if c.check_name == "model_loading")
        assert model_check.status == PreflightCheckStatus.FAIL
        assert model_check.reason_code == PreflightReasonCode.MODEL_LOAD_FAILED
        assert any("model loading failed" in f.lower() for f in report.failures)


# ==============================================================================
# RED TEAM FIXTURE: CUDA / device initialization failure
# ==============================================================================
def test_red_team_fixture_cuda_initialization_failure(tmp_path: Path):
    """CUDA device initialization failure produces MODEL_LOAD_FAILED and blocks start."""
    checker = PreflightChecker(storage_dir=tmp_path / "storage")

    mock_fw = MagicMock()
    with patch.dict("sys.modules", {"faster_whisper": mock_fw}), patch(
        "nas_air_intelligence.transcription.SpeechTranscriber._load_model",
        side_effect=RuntimeError("CUDA failed with error: no CUDA-capable device is detected"),
    ):
        report = checker.run_preflight(
            mode=PreflightMode.CAPTURE_AND_ANALYSIS,
            analyzer="whisper",
        )
        assert report.blocked is True
        assert PreflightReasonCode.MODEL_LOAD_FAILED in report.reason_codes
        model_check = next(c for c in report.checks if c.check_name == "model_loading")
        assert model_check.status == PreflightCheckStatus.FAIL
        assert model_check.reason_code == PreflightReasonCode.MODEL_LOAD_FAILED
        assert any("cuda" in f.lower() for f in report.failures)


# ==============================================================================
# RED TEAM FIXTURE 3: transcription crashes on every chunk
# ==============================================================================
def test_red_team_fixture_3_transcription_crashes_on_every_chunk(tmp_path: Path):
    """If transcription crashes on every chunk, programming analysis is strictly BLOCKED."""
    db, session_id = _setup_session(tmp_path, chunk_count=11)
    chunks = db.chunks(session_id)

    # Record transcription incidents on all 11 chunks
    for c in chunks:
        db.add_incident(
            session_id,
            "transcription_incident",
            f"chunk_id={c['id']} error=TranscriberProcessCrashed",
        )
    db.finish_session(session_id, "completed")

    analysis = build_programming_analysis(
        db, session_id, expects_transcription=True, is_dependency_unavailable=False
    )
    status = analysis["status"]
    assert status["capture_status"] == CaptureStatus.COMPLETED.value
    assert status["transcription_status"] == TranscriptionStatus.FAILED.value
    assert status["classification_status"] == ClassificationStatus.UNAVAILABLE.value
    assert status["programming_analysis_status"] == ProgrammingAnalysisStatus.BLOCKED.value
    assert status["decision_readiness"] == DecisionReadiness.NOT_READY.value

    # INVARIANT: Technical failure must never become programming evidence
    assert analysis["program_candidates"] == []
    assert analysis["clock_patterns"] == []

    # Assert no speech=0 claim or non-speech=100 claim
    for dp in analysis["dayparts"]:
        assert dp["speech_seconds"] is None
        assert dp["unknown_seconds"] is None
        assert dp["observations"] == []

    for ins in analysis["insights"]:
        assert "0.0%" not in ins["observation"]
        assert "100.0%" not in ins["observation"]
        assert ins["nas_fm_planning_input"] is None
        assert ins["classification"] == EvidenceClass.OBSERVED.value


# ==============================================================================
# RED TEAM FIXTURE 4: partial transcription
# ==============================================================================
def test_red_team_fixture_4_partial_transcription(tmp_path: Path):
    """Partial transcription degradation yields LIMITED classification and LIMITED readiness."""
    db, session_id = _setup_session(tmp_path, chunk_count=10)
    chunks = db.chunks(session_id)

    # 4 chunks failed, 6 succeeded
    for c in chunks[:4]:
        db.add_incident(
            session_id,
            "transcription_incident",
            f"chunk_id={c['id']} error=Timeout",
        )
    for c in chunks[4:]:
        db.add_events(
            [
                {
                    "id": f"ev_{c['id']}",
                    "session_id": session_id,
                    "chunk_id": c["id"],
                    "kind": "speech",
                    "start_offset": 0.0,
                    "end_offset": 30.0,
                    "confidence": 0.85,
                    "label": "faster-whisper",
                    "text": "نشرة الأخبار",
                }
            ]
        )
    db.finish_session(session_id, "completed")

    analysis = build_programming_analysis(db, session_id, expects_transcription=True)
    status = analysis["status"]
    assert status["transcription_status"] == TranscriptionStatus.DEGRADED.value
    assert status["classification_status"] == ClassificationStatus.LIMITED.value
    assert status["decision_readiness"] == DecisionReadiness.LIMITED.value


# ==============================================================================
# RED TEAM FIXTURE 5: timeline gaps
# ==============================================================================
def test_red_team_fixture_5_timeline_gaps(tmp_path: Path):
    """Large gaps between chunks are measured accurately; coverage <= 100%."""
    t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC_TZ)
    chunks = [
        {"id": "c1", "started_at": isoformat(t0), "duration_seconds": 60.0},
        {
            "id": "c2",
            "started_at": isoformat(t0 + timedelta(seconds=120)),
            "duration_seconds": 60.0,
        },
        {
            "id": "c3",
            "started_at": isoformat(t0 + timedelta(seconds=240)),
            "duration_seconds": 60.0,
        },
    ]
    segments = build_timeline(chunks, [])
    metrics = compute_timeline_coverage(chunks, segments)

    assert metrics.captured_seconds == 180.0
    assert metrics.unique_covered_seconds == 180.0
    assert metrics.gap_seconds == 120.0
    assert metrics.coverage_percent <= 100.0


# ==============================================================================
# RED TEAM FIXTURE 6: timeline overlaps
# ==============================================================================
def test_red_team_fixture_6_timeline_overlaps(tmp_path: Path):
    """Overlapping chunk timestamps: unique covered seconds dedups; coverage NEVER > 100%."""
    t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC_TZ)
    # Three 60-second chunks that overlap by 10s each
    chunks = [
        {"id": "c1", "started_at": isoformat(t0), "duration_seconds": 60.0},
        {"id": "c2", "started_at": isoformat(t0 + timedelta(seconds=50)), "duration_seconds": 60.0},
        {
            "id": "c3",
            "started_at": isoformat(t0 + timedelta(seconds=100)),
            "duration_seconds": 60.0,
        },
    ]
    segments = build_timeline(chunks, [])
    metrics = compute_timeline_coverage(chunks, segments)

    assert metrics.captured_seconds == 180.0
    assert metrics.overlap_seconds == 20.0
    # Overlap dedup: total interval span is 160s, captured is 180s
    assert metrics.unique_covered_seconds <= metrics.captured_seconds
    assert metrics.coverage_percent <= 100.0
    assert metrics.coverage_percent == pytest.approx((160.0 / 180.0) * 100.0, abs=0.1)


# ==============================================================================
# RED TEAM FIXTURE 7: insufficient daypart sample
# ==============================================================================
def test_red_team_fixture_7_insufficient_daypart_sample():
    """A 10-minute sample must NEVER be allowed to characterize a multi-hour daypart."""
    suff = assess_daypart_sufficiency(sample_seconds=600.0)
    assert suff.sufficiency_status == SufficiencyStatus.INSUFFICIENT_SAMPLE
    assert suff.coverage_ratio == pytest.approx(600.0 / 14400.0, rel=1e-3)
    assert suff.expected_window_seconds == 14400.0
    assert "Insufficient" in (suff.note or "")

    clock_suff = assess_clock_sufficiency(sample_seconds=600.0)
    assert clock_suff.sufficiency_status == SufficiencyStatus.INSUFFICIENT_SAMPLE


# ==============================================================================
# RED TEAM FIXTURE 8 / ACCEPTANCE: Al Nakhla 10-Minute Workbook Failure Scenario
# ==============================================================================
def test_acceptance_al_nakhla_10_minute_failure_scenario(tmp_path: Path):
    """Exact scenario from the Al Nakhla 10-minute workbook:
    11 chunks captured, successful capture, transcription dependency unavailable.

    Expected:
    - capture_status = COMPLETED
    - transcription_status = FAILED (or UNAVAILABLE)
    - classification_status = UNAVAILABLE
    - programming_analysis_status = BLOCKED
    - decision_readiness = NOT_READY
    - NO speech=0 claim
    - NO non-speech=100 claim
    - NO programming pattern conclusion
    - NO NAS FM planning recommendation
    - Report/export explains exactly why analysis is unavailable.
    """
    db, session_id = _setup_session(
        tmp_path, chunk_count=11, chunk_duration=60.0, target_seconds=600.0
    )
    chunks = db.chunks(session_id)

    # 11 chunks captured cleanly, but transcription failed on every chunk
    for c in chunks:
        db.add_incident(
            session_id,
            "transcription_incident",
            f"chunk_id={c['id']} error=faster-whisper dependency missing or model failed to load",
        )
    db.finish_session(session_id, "completed")

    # Generate analytical report and exports
    report, json_path, md_path, problems = write_agent_report(
        db,
        session_id,
        tmp_path / "reports",
        requested_seconds=600.0,
        expects_transcription=True,
        is_dependency_unavailable=True,
    )

    validity = report["validity"]
    assert validity["capture_status"] == CaptureStatus.COMPLETED.value
    assert validity["transcription_status"] in (
        TranscriptionStatus.FAILED.value,
        TranscriptionStatus.UNAVAILABLE.value,
    )
    assert validity["classification_status"] == ClassificationStatus.UNAVAILABLE.value
    assert validity["programming_analysis_status"] == ProgrammingAnalysisStatus.BLOCKED.value
    assert validity["decision_readiness"] == DecisionReadiness.NOT_READY.value

    # Build programming intelligence directly
    programming = build_programming_analysis(
        db, session_id, expects_transcription=True, is_dependency_unavailable=True
    )
    status = programming["status"]
    assert status["capture_status"] == "COMPLETED"
    assert status["transcription_status"] in ("FAILED", "UNAVAILABLE")
    assert status["classification_status"] == "UNAVAILABLE"
    assert status["programming_analysis_status"] == "BLOCKED"
    assert status["decision_readiness"] == "NOT_READY"

    # Assert NO speech=0 claim and NO non-speech=100 claim anywhere
    md_text = md_path.read_text(encoding="utf-8")
    assert "Speech-classified time is 0.0%" not in md_text
    assert "Non-speech/unknown audio dominates" not in md_text
    assert "0.0% of monitored time" not in md_text
    assert "100.0% of monitored time" not in md_text

    # Assert NO program candidates or clock patterns were invented
    assert programming["program_candidates"] == []
    assert programming["clock_patterns"] == []

    # Assert NO NAS FM planning recommendations were created from unavailable evidence
    insights = programming["insights"]
    planning_inputs = [
        i.get("nas_fm_planning_input") for i in insights if i.get("nas_fm_planning_input")
    ]
    assert planning_inputs == []

    # Assert report explains exactly why analysis is unavailable
    assert "BLOCKED" in md_text
    assert "UNAVAILABLE" in md_text

    # Write and test Excel export workbook
    excel_path = write_workbook(db, session_id, tmp_path / "exports", requested_seconds=600.0)
    assert excel_path.exists()

    # Validate analysis.json schema Draft 2020-12
    analysis_json_file = tmp_path / "reports" / "analysis.json"
    assert analysis_json_file.exists()
    analysis_data = json.loads(analysis_json_file.read_text(encoding="utf-8"))
    is_valid, errors = validate_analysis_json(analysis_data)
    assert is_valid is True, f"analysis.json schema validation failed: {errors}"


# ==============================================================================
# HEALTHY ANALYTICAL FIXTURE: Confirm normal operation works
# ==============================================================================
def test_healthy_analytical_fixture_full_validity(tmp_path: Path):
    """When capture and transcription succeed with sufficient sample,
    decision_readiness is READY (or LIMITED for single-session),
    and programming intelligence produces valid traceable insights.
    """
    db, session_id = _setup_session(
        tmp_path,
        chunk_count=24,  # 2 hours: 24 chunks x 300s = 7200s
        chunk_duration=300.0,
        target_seconds=7200.0,
    )
    chunks = db.chunks(session_id)

    # Add realistic alternating speech and silence across chunks
    for i, c in enumerate(chunks):
        c_id = c["id"]
        db.add_events(
            [
                {
                    "id": f"speech_{i}_1",
                    "session_id": session_id,
                    "chunk_id": c_id,
                    "kind": "speech",
                    "start_offset": 0.0,
                    "end_offset": 90.0,
                    "confidence": 0.88,
                    "label": "faster-whisper",
                    "text": "هنا بغداد، استمعتم إلى نشرة الأخبار الرياضية.",
                    "metadata": {"model": "tiny", "device": "cuda", "compute_type": "float16"},
                },
                {
                    "id": f"silence_{i}",
                    "session_id": session_id,
                    "chunk_id": c_id,
                    "kind": "silence",
                    "start_offset": 90.0,
                    "end_offset": 100.0,
                    "confidence": 1.0,
                    "label": "ffmpeg-silencedetect",
                    "metadata": {},
                },
                {
                    "id": f"speech_{i}_2",
                    "session_id": session_id,
                    "chunk_id": c_id,
                    "kind": "speech",
                    "start_offset": 100.0,
                    "end_offset": 250.0,
                    "confidence": 0.92,
                    "label": "faster-whisper",
                    "text": "برنامج صباح الخير، ننتقل الآن إلى فقرة الطقس.",
                    "metadata": {"model": "tiny", "device": "cuda", "compute_type": "float16"},
                },
            ]
        )
    db.finish_session(session_id, "completed")

    analysis = build_programming_analysis(db, session_id, expects_transcription=True)
    status = analysis["status"]
    assert status["capture_status"] == CaptureStatus.COMPLETED.value
    assert status["transcription_status"] == TranscriptionStatus.COMPLETED.value
    assert status["classification_status"] == ClassificationStatus.COMPLETED.value
    assert status["programming_analysis_status"] == ProgrammingAnalysisStatus.COMPLETED.value

    # Full traceability verification: Insight -> Content Block -> Timeline Event -> Chunk -> Audio
    blocks = analysis["content_blocks"]
    assert len(blocks) > 0
    b0 = blocks[0]
    assert b0["id"].startswith("blk-")
    assert len(b0["evidence_event_ids"]) > 0
    assert len(b0["chunk_ids"]) > 0
    assert len(b0["source_audio_files"]) > 0

    insights = analysis["insights"]
    assert len(insights) > 0
    for ins in insights:
        assert ins["classification"] in ("OBSERVED", "INFERRED", "NAS_FM_PLANNING_INPUT")
        assert 0.0 <= ins["confidence"] <= 1.0
        assert ins["confidence_basis"] != ""
        assert len(ins["evidence_refs"]) > 0

    # Validate analysis.json export
    report, json_path, md_path, problems = write_agent_report(
        db, session_id, tmp_path / "reports", requested_seconds=7200.0, expects_transcription=True
    )
    analysis_json_file = tmp_path / "reports" / "analysis.json"
    assert analysis_json_file.exists()
    analysis_data = json.loads(analysis_json_file.read_text(encoding="utf-8"))
    is_valid, errors = validate_analysis_json(analysis_data)
    assert is_valid is True, f"JSON Schema validation errors: {errors}"
