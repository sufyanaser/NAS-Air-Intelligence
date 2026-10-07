"""Phase 5 Programming Intelligence: content blocks, program candidates, clock patterns,
dayparts, and diagnostics - all pure functions of chunks/events, deterministic, evidence-
and-confidence-bearing, and never claiming a program name or music/jingle/ad identity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nas_air_intelligence.agent.programming import (
    build_programming_analysis,
    clock_patterns,
    content_blocks,
    daypart_analysis,
    presenter_return_intervals,
    program_candidates,
    programming_diagnostics,
)
from nas_air_intelligence.agent.timeline import build_timeline, station_timezone
from nas_air_intelligence.db import Database
from nas_air_intelligence.util import isoformat


def _chunk(chunk_id: str, start: datetime, seconds: float) -> dict[str, Any]:
    return {
        "id": chunk_id, "started_at": isoformat(start), "duration_seconds": seconds,
        "analyzed": 1,
    }  # fmt: skip


def _event(event_id: str, chunk_id: str, kind: str, a: float, b: float, **extra: Any):
    return {
        "id": event_id, "chunk_id": chunk_id, "kind": kind, "start_offset": a, "end_offset": b,
        "confidence": extra.get("confidence"), "text": extra.get("text"),
        "fingerprint": extra.get("fingerprint"),
    }  # fmt: skip


UTC_TZ = station_timezone("UTC")


# ------------------------------------------------------------------------------ content blocks
def test_content_block_classifies_speech_heavy_and_preserves_evidence_ids():
    t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
    chunks = [_chunk("c1", t0, 60.0)]
    events = [
        _event("e1", "c1", "speech", 0.0, 30.0, confidence=0.8, text="a"),
        _event("e2", "c1", "speech", 30.0, 60.0, confidence=0.8, text="b"),
    ]
    blocks = content_blocks(chunks, events)
    assert len(blocks) == 1
    assert blocks[0].block_type == "speech-heavy"
    assert set(blocks[0].evidence_event_ids) == {"e1", "e2"}
    assert blocks[0].confidence == 1.0  # no "unknown" lean involved


def test_content_block_folds_a_short_interruption_but_not_a_long_one():
    t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
    chunks = [_chunk("c1", t0, 140.0)]
    events = [
        _event("e1", "c1", "speech", 0.0, 60.0, confidence=0.9, text="a"),
        _event("e2", "c1", "silence", 60.0, 70.0),  # 10s pause: folded into the speech block
        _event("e3", "c1", "speech", 70.0, 140.0, confidence=0.9, text="b"),
    ]
    blocks = content_blocks(chunks, events)
    assert len(blocks) == 1
    assert blocks[0].block_type == "speech-heavy"
    assert blocks[0].notes and "interruption" in blocks[0].notes

    chunks_long = [_chunk("c1", t0, 200.0)]
    events_long_gap = [
        _event("e1", "c1", "speech", 0.0, 60.0, confidence=0.9, text="a"),
        _event("e2", "c1", "unknown", 60.0, 140.0),  # 80s: a real second block
        _event("e3", "c1", "speech", 140.0, 200.0, confidence=0.9, text="b"),
    ]
    blocks2 = content_blocks(chunks_long, events_long_gap)
    assert len(blocks2) == 3
    assert [b.block_type for b in blocks2] == ["speech-heavy", "non-speech/unknown", "speech-heavy"]


def test_content_block_type_mixed_when_no_lean_dominates():
    t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
    chunks = [_chunk("c1", t0, 100.0)]
    events = [
        _event("e1", "c1", "speech", 0.0, 50.0, confidence=0.9, text="a"),
        _event("e2", "c1", "unknown", 50.0, 100.0),
    ]
    blocks = content_blocks(chunks, events)
    assert len(blocks) == 1 and blocks[0].block_type == "mixed"


def test_content_block_never_names_a_program():
    t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
    chunks = [_chunk("c1", t0, 60.0)]
    events = [_event("e1", "c1", "speech", 0.0, 60.0, confidence=0.9, text="hello")]
    for block in content_blocks(chunks, events):
        d = block.to_dict()
        assert d["block_type"] in {"speech-heavy", "non-speech/unknown", "silence/interruption",
                                    "mixed"}  # fmt: skip


# -------------------------------------------------------------------------- program candidates
def _hourly_blocks(hours: int, minute: int, duration: float, block_type: str = "speech-heavy"):
    """Synthetic chunks/events producing one same-typed content block per hour at `minute`."""
    t0 = datetime(2026, 1, 5, 6, 0, 0, tzinfo=UTC)
    chunks, events = [], []
    kind = "speech" if block_type == "speech-heavy" else "unknown"
    for h in range(hours):
        start = t0 + timedelta(hours=h, minutes=minute)
        cid = f"c{h}"
        chunks.append(_chunk(cid, start, duration))
        events.append(_event(f"e{h}", cid, kind, 0.0, duration, confidence=0.8, text="x"))
    return chunks, events


def test_program_candidate_requires_recurrence_across_distinct_hours():
    chunks, events = _hourly_blocks(hours=4, minute=15, duration=120.0)
    blocks = content_blocks(chunks, events)
    candidates = program_candidates(blocks, UTC_TZ)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.bucket_minute == 10  # 15 rounds down to the 10-minute bucket
    assert candidate.occurrences == 4
    assert 0.0 < candidate.confidence <= 0.9
    assert len(candidate.evidence) == 4


def test_program_candidate_is_not_reported_from_a_single_occurrence():
    t0 = datetime(2026, 1, 5, 6, 15, 0, tzinfo=UTC)
    chunks = [_chunk("c1", t0, 120.0)]
    events = [_event("e1", "c1", "speech", 0.0, 120.0, confidence=0.8, text="x")]
    blocks = content_blocks(chunks, events)
    assert program_candidates(blocks, UTC_TZ) == []


def test_program_candidate_ignores_short_blocks():
    chunks, events = _hourly_blocks(hours=3, minute=15, duration=10.0)  # below the 60s minimum
    blocks = content_blocks(chunks, events)
    assert program_candidates(blocks, UTC_TZ) == []


# ------------------------------------------------------------------------------ clock patterns
def test_presenter_return_interval_uses_blocks_not_raw_whisper_fragments():
    """A conversation Whisper splits into many short segments must not look like the presenter
    returning every few seconds - the interval is measured between content blocks."""
    t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
    chunks = [_chunk("c1", t0, 600.0)]
    events = []
    # 20 short speech fragments 1s apart within one continuous speech-heavy block.
    for i in range(20):
        events.append(_event(f"frag{i}", "c1", "speech", i * 2.0, i * 2.0 + 1.0, confidence=0.8,
                              text="x"))  # fmt: skip
    # A second, separate speech-heavy block ~5 minutes later in a new chunk.
    t1 = t0 + timedelta(seconds=600)
    chunks.append(_chunk("c2", t1, 60.0))
    events.append(_event("e2", "c2", "speech", 0.0, 60.0, confidence=0.8, text="y"))

    blocks = content_blocks(chunks, events)
    intervals = presenter_return_intervals(blocks)
    assert intervals and all(i > 60 for i in intervals), intervals  # minutes, not seconds


def test_clock_pattern_references_occurrences(tmp_path):
    chunks, events = _hourly_blocks(hours=3, minute=20, duration=90.0)
    blocks = content_blocks(chunks, events)
    segments = build_timeline(chunks, events)
    patterns = clock_patterns(events, chunks, blocks, segments, UTC_TZ)
    assert all(p.occurrences > 0 for p in patterns)
    assert all(0.0 <= p.confidence <= 0.9 for p in patterns)
    presenter_patterns = [p for p in patterns if p.pattern_type == "presenter_return_interval"]
    assert presenter_patterns and presenter_patterns[0].occurrences == 3


# ---------------------------------------------------------------------------------- dayparts
def test_daypart_totals_sum_to_monitored_duration_and_respect_the_night_wraparound():
    # One chunk entirely inside Night (23:00) and one entirely inside Overnight (03:00) on the
    # following calendar day - this is exactly the boundary the naive hour+24 arithmetic broke.
    night_start = datetime(2026, 1, 5, 23, 0, 0, tzinfo=UTC)
    overnight_start = datetime(2026, 1, 6, 3, 0, 0, tzinfo=UTC)
    chunks = [_chunk("night", night_start, 1800.0), _chunk("over", overnight_start, 1800.0)]
    events = [
        _event("e1", "night", "speech", 0.0, 1800.0, confidence=0.8, text="n"),
        _event("e2", "over", "unknown", 0.0, 1800.0),
    ]
    segments = build_timeline(chunks, events)
    blocks = content_blocks(chunks, events)
    patterns = clock_patterns(events, chunks, blocks, segments, UTC_TZ)
    candidates = program_candidates(blocks, UTC_TZ)
    stats = {d.name: d for d in daypart_analysis(segments, blocks, candidates, patterns, UTC_TZ)}

    assert set(stats) == {"Night", "Overnight"}
    assert stats["Night"].monitored_seconds == pytest.approx(1800.0)
    assert stats["Night"].speech_seconds == pytest.approx(1800.0)
    assert stats["Overnight"].monitored_seconds == pytest.approx(1800.0)
    assert stats["Overnight"].unknown_seconds == pytest.approx(1800.0)
    for stat in stats.values():
        assert 0.0 <= stat.confidence <= 0.8


def test_daypart_spanning_midnight_is_attributed_to_a_single_night_window():
    """A chunk that starts at 23:30 and runs 2 hours (crossing midnight) must still land in
    one Night window, not be miscounted against two different calendar days' Night buckets."""
    start = datetime(2026, 1, 5, 23, 30, 0, tzinfo=UTC)
    chunks = [_chunk("c1", start, 3600.0)]
    events = [_event("e1", "c1", "speech", 0.0, 3600.0, confidence=0.8, text="x")]
    segments = build_timeline(chunks, events)
    blocks = content_blocks(chunks, events)
    stats = daypart_analysis(segments, blocks, [], [], UTC_TZ)
    assert len(stats) == 1
    assert stats[0].name == "Night"
    assert stats[0].monitored_seconds == pytest.approx(3600.0)


# ------------------------------------------------------------------------------- diagnostics
def test_diagnostics_keep_observed_inferred_and_recommendation_separate():
    chunks, events = _hourly_blocks(hours=4, minute=10, duration=200.0)
    blocks = content_blocks(chunks, events)
    segments = build_timeline(chunks, events)
    patterns = clock_patterns(events, chunks, blocks, segments, UTC_TZ)
    candidates = program_candidates(blocks, UTC_TZ)
    insights = programming_diagnostics(segments, blocks, candidates, patterns)

    assert {i.type for i in insights} <= {"OBSERVED", "INFERRED", "RECOMMENDATION"}
    assert any(i.type == "OBSERVED" for i in insights)
    assert any(i.type == "INFERRED" for i in insights)
    for insight in insights:
        assert insight.evidence  # every insight carries evidence
        if insight.type == "OBSERVED":
            assert insight.nas_fm_planning_input is None  # never attached to a raw OBSERVED fact
        if insight.nas_fm_planning_input:
            assert insight.type in {"INFERRED", "RECOMMENDATION"}


def test_diagnostics_never_claim_music_jingle_or_advertisement():
    chunks, events = _hourly_blocks(hours=5, minute=0, duration=300.0, block_type="non-speech")
    blocks = content_blocks(chunks, events)
    segments = build_timeline(chunks, events)
    patterns = clock_patterns(events, chunks, blocks, segments, UTC_TZ)
    candidates = program_candidates(blocks, UTC_TZ)
    insights = programming_diagnostics(segments, blocks, candidates, patterns)
    banned = {"music", "jingle", "advertisement", "promo", "song"}
    for insight in insights:
        text = (insight.observation + " " + (insight.nas_fm_planning_input or "")).lower()
        assert not banned & set(text.replace(",", " ").replace(".", " ").split())


# ------------------------------------------------------------------------------------- top level
def _real_session(tmp_path) -> tuple[Database, str]:
    db = Database(tmp_path / "study.db")
    station = db.upsert_station("Test FM", "https://s/live.mp3")
    session_id = db.create_session(station, 7200.0, 300, str(tmp_path / "out"))
    t0 = datetime(2026, 1, 5, 10, 0, 0, tzinfo=UTC)
    for h in range(2):
        for m in (0, 20, 40):
            start = t0 + timedelta(hours=h, minutes=m)
            label = f"c{h}-{m}"
            chunk_id = db.add_chunk(
                session_id, f"/tmp/{label}.mp3", isoformat(start),
                isoformat(start + timedelta(seconds=180)), 180.0, f"sha-{label}",
            )  # fmt: skip
            db.add_events([
                {"id": f"e-{label}", "session_id": session_id, "chunk_id": chunk_id,
                 "kind": "speech", "start_offset": 0.0, "end_offset": 150.0, "confidence": 0.8,
                 "text": "hello", "fingerprint": None, "metadata": {}},
            ])  # fmt: skip
    db.finish_session(session_id, "completed")
    return db, session_id


def test_build_programming_analysis_is_reproducible(tmp_path):
    db, session_id = _real_session(tmp_path)
    first = build_programming_analysis(db, session_id)
    second = build_programming_analysis(db, session_id)
    assert first == second


def test_build_programming_analysis_matches_study_summary_shape(tmp_path):
    db, session_id = _real_session(tmp_path)
    result = build_programming_analysis(db, session_id)
    summary = result["study_summary"]
    assert set(summary) == {
        "duration_seconds",
        "timeline_coverage_pct",
        "content_block_count",
        "program_candidate_count",
        "clock_pattern_count",
        "dayparts_covered",
        "incidents",
    }
    assert summary["timeline_coverage_pct"] == pytest.approx(100.0, abs=0.5)
    assert summary["content_block_count"] >= 1
    assert result["station"] == "Test FM"


def test_build_programming_analysis_unknown_session_raises(tmp_path):
    db = Database(tmp_path / "x.db")
    db.initialize()
    with pytest.raises(KeyError):
        build_programming_analysis(db, "does-not-exist")
