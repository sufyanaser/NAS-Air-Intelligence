"""Phase 5 — Programming Intelligence: study-ready structures built from the timeline.

SQLite remains the source of truth; everything here is a pure, deterministic read of the
chunks/events already in the database - calling this twice on the same session produces
identical output. Every insight keeps the evidence hierarchy from
radio-monitoring-agent-context.md section 5 explicit:

    OBSERVED              - a direct measurement (a ratio, a count).
    INFERRED              - an interpretation of a recurring pattern, with evidence/occurrences.
    NAS_FM_PLANNING_INPUT - a cautious, non-copying suggestion, only attached to an INFERRED row.

Hard analytical gates:
- If transcription is required and failed, speech is NEVER reported as 0,
  non-speech is NEVER inferred, and programming analysis is strictly BLOCKED.
- UNKNOWN is a valid evidence state and NEVER becomes non-speech, music,
  advertisement, jingle, presenter, or program.
- Multi-hour daypart conclusions are strictly blocked on short samples (INSUFFICIENT_SAMPLE).
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any

from ..db import Database
from ..util import isoformat, parse_iso
from .sufficiency import (
    assess_candidate_sufficiency,
    assess_clock_sufficiency,
    assess_daypart_sufficiency,
)
from .timeline import (
    TimelineSegment,
    build_timeline,
    compute_timeline_coverage,
    recurrent_candidates,
    station_timezone,
    timezone_metadata,
)
from .validity import (
    ClassificationStatus,
    EvidenceClass,
    ProgrammingAnalysisStatus,
    SufficiencyStatus,
    TranscriptionStatus,
    evaluate_analytical_validity,
)

BLOCK_SPLIT_THRESHOLD_SECONDS = 60.0
MIN_CANDIDATE_BLOCK_SECONDS = 60.0
CLOCK_BUCKET_MINUTES = 10
SPEECH_CLUSTER_BUCKET_MINUTES = 5

DAYPART_ORDER = ["Morning", "Midday", "Afternoon", "Evening", "Night", "Overnight"]
DAYPART_LABELS = {
    "Morning": "06:00–10:00",
    "Midday": "10:00–14:00",
    "Afternoon": "14:00–18:00",
    "Evening": "18:00–22:00",
    "Night": "22:00–02:00",
    "Overnight": "02:00–06:00",
}


def _lean(kind: str) -> str:
    if kind == "speech":
        return "speech"
    if kind in {"silence", "capture_gap"}:
        return "silence"
    return "unknown"  # UNKNOWN is never assumed to be non-speech or music


def _daypart_at(dt: datetime, tz: tzinfo) -> tuple[str, datetime, datetime]:
    """The (name, absolute window start, absolute window end) containing ``dt`` in ``tz``."""
    local = dt.astimezone(tz)
    hour = local.hour + local.minute / 60.0 + local.second / 3600.0
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if 6 <= hour < 10:
        return "Morning", midnight + timedelta(hours=6), midnight + timedelta(hours=10)
    if 10 <= hour < 14:
        return "Midday", midnight + timedelta(hours=10), midnight + timedelta(hours=14)
    if 14 <= hour < 18:
        return "Afternoon", midnight + timedelta(hours=14), midnight + timedelta(hours=18)
    if 18 <= hour < 22:
        return "Evening", midnight + timedelta(hours=18), midnight + timedelta(hours=22)
    if 2 <= hour < 6:
        return "Overnight", midnight + timedelta(hours=2), midnight + timedelta(hours=6)
    if hour >= 22:
        return "Night", midnight + timedelta(hours=22), midnight + timedelta(days=1, hours=2)
    return "Night", midnight - timedelta(hours=2), midnight + timedelta(hours=2)


def _daypart_name(dt: datetime, tz: tzinfo) -> str:
    return _daypart_at(dt, tz)[0]


@dataclass
class ContentBlock:
    id: str
    start: datetime
    end: datetime
    block_type: str  # "speech-heavy" | "silence" | "unknown" | "mixed"
    lean_seconds: dict[str, float]
    evidence_event_ids: list[str]
    chunk_ids: list[str] = field(default_factory=list)
    source_audio_files: list[str] = field(default_factory=list)
    confidence_basis: str = ""
    limitations: list[str] = field(default_factory=list)
    notes: str | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def confidence(self) -> float:
        total = sum(self.lean_seconds.values())
        if total <= 0:
            return 0.0
        firm = total - self.lean_seconds.get("unknown", 0.0)
        return round(max(0.0, min(1.0, firm / total)), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": isoformat(self.start),
            "end": isoformat(self.end),
            "duration_seconds": round(self.duration_seconds, 1),
            "block_type": self.block_type,
            "confidence": self.confidence,
            "confidence_basis": self.confidence_basis
            or "Duration-weighted share of verified evidence",
            "evidence_event_ids": self.evidence_event_ids,
            "chunk_ids": self.chunk_ids,
            "source_audio_files": self.source_audio_files,
            "limitations": self.limitations,
            "notes": self.notes,
        }


@dataclass
class ProgramCandidate:
    id: str
    bucket_minute: int
    block_type: str
    occurrences: int
    mean_duration_seconds: float
    evidence: list[str]
    confidence: float
    confidence_basis: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    sufficiency_status: SufficiencyStatus = SufficiencyStatus.LIMITED
    sample_seconds: float = 0.0
    expected_window_seconds: float = 7200.0
    coverage_ratio: float = 0.0
    sessions_count: int = 1
    days_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bucket": f":{self.bucket_minute:02d}",
            "block_type": self.block_type,
            "occurrences": self.occurrences,
            "mean_duration_seconds": round(self.mean_duration_seconds, 1),
            "evidence": self.evidence,
            "confidence": round(self.confidence, 3),
            "confidence_basis": self.confidence_basis,
            "evidence_refs": self.evidence_refs,
            "limitations": self.limitations,
            "sufficiency_status": self.sufficiency_status.value,
            "sample_seconds": round(self.sample_seconds, 1),
            "expected_window_seconds": round(self.expected_window_seconds, 1),
            "coverage_ratio": round(self.coverage_ratio, 4),
            "sessions_count": self.sessions_count,
            "days_count": self.days_count,
        }


@dataclass
class ClockPattern:
    pattern_type: str
    description: str
    occurrences: int
    evidence: list[str]
    confidence: float
    confidence_basis: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    sufficiency_status: SufficiencyStatus = SufficiencyStatus.LIMITED
    sample_seconds: float = 0.0
    expected_window_seconds: float = 3600.0
    coverage_ratio: float = 0.0
    sessions_count: int = 1
    days_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "occurrences": self.occurrences,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 3),
            "confidence_basis": self.confidence_basis,
            "evidence_refs": self.evidence_refs,
            "limitations": self.limitations,
            "sufficiency_status": self.sufficiency_status.value,
            "sample_seconds": round(self.sample_seconds, 1),
            "expected_window_seconds": round(self.expected_window_seconds, 1),
            "coverage_ratio": round(self.coverage_ratio, 4),
            "sessions_count": self.sessions_count,
            "days_count": self.days_count,
        }


@dataclass
class DaypartStat:
    name: str
    window: str
    monitored_seconds: float
    speech_seconds: float | None
    unknown_seconds: float | None
    silence_seconds: float | None
    avg_block_duration_seconds: float | None
    presenter_return_interval_seconds: float | None
    recurrent_element_count: int
    program_candidate_count: int
    confidence: float
    confidence_basis: str
    evidence_refs: list[str]
    limitations: list[str]
    sufficiency_status: SufficiencyStatus
    sample_seconds: float
    expected_window_seconds: float
    coverage_ratio: float
    sessions_count: int
    days_count: int
    observations: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "daypart": self.name,
            "window": self.window,
            "monitored_seconds": round(self.monitored_seconds, 1),
            "speech_seconds": (
                round(self.speech_seconds, 1) if self.speech_seconds is not None else None
            ),
            "unknown_seconds": (
                round(self.unknown_seconds, 1) if self.unknown_seconds is not None else None
            ),
            "silence_seconds": (
                round(self.silence_seconds, 1) if self.silence_seconds is not None else None
            ),
            "avg_block_duration_seconds": (
                round(self.avg_block_duration_seconds, 1)
                if self.avg_block_duration_seconds is not None
                else None
            ),
            "presenter_return_interval_seconds": (
                round(self.presenter_return_interval_seconds, 1)
                if self.presenter_return_interval_seconds is not None
                else None
            ),
            "recurrent_element_count": self.recurrent_element_count,
            "program_candidate_count": self.program_candidate_count,
            "confidence": round(self.confidence, 3),
            "confidence_basis": self.confidence_basis,
            "evidence_refs": self.evidence_refs,
            "limitations": self.limitations,
            "sufficiency_status": self.sufficiency_status.value,
            "sample_seconds": round(self.sample_seconds, 1),
            "expected_window_seconds": round(self.expected_window_seconds, 1),
            "coverage_ratio": round(self.coverage_ratio, 4),
            "sessions_count": self.sessions_count,
            "days_count": self.days_count,
            "observations": self.observations,
        }


@dataclass
class Insight:
    classification: EvidenceClass  # OBSERVED | INFERRED | NAS_FM_PLANNING_INPUT
    observation: str
    evidence: str
    occurrences: int
    confidence: float
    confidence_basis: str
    evidence_refs: list[str]
    limitations: list[str] = field(default_factory=list)
    nas_fm_planning_input: str | None = None
    type: str = ""  # compatibility alias for classification

    def __post_init__(self) -> None:
        if not self.type:
            self.type = self.classification.value
        if not (0.0 <= self.confidence <= 1.0):
            self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "type": self.type or self.classification.value,
            "observation": self.observation,
            "evidence": self.evidence,
            "occurrences": self.occurrences,
            "confidence": round(self.confidence, 3),
            "confidence_basis": self.confidence_basis,
            "evidence_refs": self.evidence_refs,
            "limitations": self.limitations,
            "nas_fm_planning_input": self.nas_fm_planning_input,
        }


# ------------------------------------------------------------------------------ content blocks
def content_blocks(
    chunks: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[ContentBlock]:
    """Group adjacent timeline segments into honestly-typed blocks with full traceability."""
    segments = build_timeline(chunks, events)
    chunk_map: dict[str, dict[str, Any]] = {c["id"]: c for c in chunks}
    chunk_window: dict[str, tuple[datetime, datetime]] = {
        c["id"]: (
            parse_iso(c["started_at"]),
            parse_iso(c["started_at"]) + timedelta(seconds=float(c["duration_seconds"])),
        )
        for c in chunks
    }

    def _events_and_chunks_in(
        start: datetime, end: datetime
    ) -> tuple[list[str], list[str], list[str]]:
        event_ids, chunk_ids, audio_files = [], set(), set()
        for event in events:
            c_id = event.get("chunk_id")
            window = chunk_window.get(c_id) if c_id else None
            if not window or not c_id:
                continue
            c_start, _ = window
            e_start = c_start + timedelta(seconds=float(event["start_offset"]))
            e_end = c_start + timedelta(seconds=float(event["end_offset"]))
            if e_start < end and e_end > start:
                event_ids.append(event["id"])
                chunk_ids.add(c_id)
                chunk_obj = chunk_map.get(c_id)
                if chunk_obj and chunk_obj.get("path"):
                    audio_files.add(chunk_obj["path"])
        # Also include chunks that overlap the block even if no events in that subsegment
        for c_id, (c_start, c_end) in chunk_window.items():
            if c_start < end and c_end > start:
                chunk_ids.add(c_id)
                chunk_obj = chunk_map.get(c_id)
                if chunk_obj and chunk_obj.get("path"):
                    audio_files.add(chunk_obj["path"])
        return event_ids, sorted(chunk_ids), sorted(audio_files)

    blocks: list[ContentBlock] = []
    members: list[TimelineSegment] = []
    block_index = 1

    def _flush() -> None:
        nonlocal block_index
        if not members:
            return
        totals: dict[str, float] = defaultdict(float)
        for seg in members:
            totals[_lean(seg.kind)] += seg.duration_seconds
        total = sum(totals.values())
        speech_frac = totals.get("speech", 0.0) / total if total else 0.0
        silence_frac = totals.get("silence", 0.0) / total if total else 0.0
        unknown_frac = totals.get("unknown", 0.0) / total if total else 0.0

        if speech_frac >= 0.6:
            block_type = "speech-heavy"
        elif silence_frac >= 0.6:
            block_type = "silence/interruption"
        elif unknown_frac >= 0.6:
            block_type = "non-speech/unknown"
        else:
            block_type = "mixed"

        start, end = members[0].start, members[-1].end
        event_ids, c_ids, audio_paths = _events_and_chunks_in(start, end)
        dominant = max(totals, key=lambda k: totals[k]) if totals else "unknown"
        folded = [s for s in members if _lean(s.kind) != dominant]
        notes = None
        if folded:
            folded_seconds = sum(s.duration_seconds for s in folded)
            notes = f"includes {len(folded)} short interruption(s) totaling {folded_seconds:.1f}s"

        limitations = []
        if block_type == "unknown":
            limitations.append("Unclassified audio interval; not verified as music or non-speech.")

        blocks.append(
            ContentBlock(
                id=f"blk-{block_index:03d}",
                start=start,
                end=end,
                block_type=block_type,
                lean_seconds=dict(totals),
                evidence_event_ids=event_ids,
                chunk_ids=c_ids,
                source_audio_files=audio_paths,
                confidence_basis=(
                    "Duration-weighted share of verified speech/silence timeline segments"
                ),
                limitations=limitations,
                notes=notes,
            )
        )
        block_index += 1

    for seg in segments:
        if seg.kind == "capture_gap":
            _flush()
            members = []
            continue
        if not members:
            members = [seg]
            continue
        totals: dict[str, float] = defaultdict(float)
        for s in members:
            totals[_lean(s.kind)] += s.duration_seconds
        dominant = max(totals, key=lambda k: totals[k])
        substantial_and_different = (
            seg.duration_seconds >= BLOCK_SPLIT_THRESHOLD_SECONDS and _lean(seg.kind) != dominant
        )
        if not substantial_and_different:
            members.append(seg)
        else:
            _flush()
            members = [seg]
    _flush()
    return blocks


# --------------------------------------------------------------------------- program candidates
def program_candidates(
    blocks: list[ContentBlock], tz: tzinfo, total_sample_seconds: float = 0.0
) -> list[ProgramCandidate]:
    """Recurring, similarly-timed, similarly-typed blocks.

    Enforces sample sufficiency: blocked if total sample is under 1 hour.
    """
    if total_sample_seconds <= 0.0 and blocks:
        total_sample_seconds = (blocks[-1].end - blocks[0].start).total_seconds()

    if total_sample_seconds < 3600.0:
        # Sample under 1 hour cannot establish multi-hour program candidates
        return []

    eligible = [
        b
        for b in blocks
        if b.block_type in {"speech-heavy", "mixed"}
        and b.duration_seconds >= MIN_CANDIDATE_BLOCK_SECONDS
    ]
    groups: dict[tuple[str, int], list[ContentBlock]] = defaultdict(list)
    for block in eligible:
        local = block.start.astimezone(tz)
        bucket = (local.minute // CLOCK_BUCKET_MINUTES) * CLOCK_BUCKET_MINUTES
        groups[(block.block_type, bucket)].append(block)

    candidates: list[ProgramCandidate] = []
    cand_index = 1
    for (block_type, bucket), members in groups.items():
        hours = {m.start.astimezone(tz).hour for m in members}
        if len(members) < 2 or len(hours) < 2:
            continue
        durations = [m.duration_seconds for m in members]
        mean_duration = statistics.mean(durations)
        stdev = statistics.pstdev(durations) if len(durations) > 1 else 0.0
        consistency = max(0.0, 1.0 - (stdev / mean_duration if mean_duration else 1.0))
        confidence = min(0.9, 0.3 + 0.15 * (len(members) - 1)) * (0.5 + 0.5 * consistency)

        suff = assess_candidate_sufficiency(
            sample_seconds=total_sample_seconds,
            occurrences=len(members),
            hours_spanned=len(hours),
        )
        evidence_refs = [f"block:{b.id}" for b in members]

        candidates.append(
            ProgramCandidate(
                id=f"cand-{cand_index:03d}",
                bucket_minute=bucket,
                block_type=block_type,
                occurrences=len(members),
                mean_duration_seconds=mean_duration,
                evidence=sorted(isoformat(m.start) for m in members),
                confidence=round(confidence, 3),
                confidence_basis=(
                    f"Recurrence across {len(hours)} distinct hours "
                    f"with duration consistency {consistency:.2f}"
                ),
                evidence_refs=evidence_refs,
                limitations=[
                    "Program candidate only; does not name a specific program or format",
                    "Requires multi-day confirmation before schedule planning",
                ],
                sufficiency_status=suff.sufficiency_status,
                sample_seconds=total_sample_seconds,
                expected_window_seconds=suff.expected_window_seconds,
                coverage_ratio=suff.coverage_ratio,
                sessions_count=1,
                days_count=1,
            )
        )
        cand_index += 1
    candidates.sort(key=lambda c: (-c.occurrences, -c.confidence))
    return candidates


# ------------------------------------------------------------------------------- clock patterns
def presenter_return_intervals(blocks: list[ContentBlock]) -> list[float]:
    """Gaps between consecutive speech-led content blocks' start times."""
    speech_led = sorted(
        (b for b in blocks if b.block_type in {"speech-heavy", "mixed"}), key=lambda b: b.start
    )
    return [
        (b.start - a.start).total_seconds()
        for a, b in zip(speech_led, speech_led[1:], strict=False)
    ]


def clock_patterns(
    events: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    blocks: list[ContentBlock],
    segments: list[TimelineSegment],
    tz: tzinfo,
    total_sample_seconds: float = 0.0,
) -> list[ClockPattern]:
    """Extract hourly broadcast clock patterns.

    Enforces sample sufficiency: blocked if total sample is under 1 hour.
    """
    if total_sample_seconds <= 0.0:
        if blocks:
            total_sample_seconds = (blocks[-1].end - blocks[0].start).total_seconds()
        elif chunks:
            total_sample_seconds = (
                parse_iso(chunks[-1]["started_at"]) - parse_iso(chunks[0]["started_at"])
            ).total_seconds() + float(chunks[-1].get("duration_seconds", 0.0))
        elif segments:
            total_sample_seconds = (segments[-1].end - segments[0].start).total_seconds()

    if total_sample_seconds < 3600.0:
        return []

    patterns: list[ClockPattern] = []
    speech_led = sorted(
        (b for b in blocks if b.block_type in {"speech-heavy", "mixed"}), key=lambda b: b.start
    )
    intervals = presenter_return_intervals(blocks)
    clock_suff = assess_clock_sufficiency(sample_seconds=total_sample_seconds)

    if len(intervals) >= 2:
        mean_interval = statistics.mean(intervals)
        stdev_interval = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
        consistency = max(0.0, 1.0 - (stdev_interval / mean_interval if mean_interval else 1.0))
        confidence = min(0.85, 0.3 + 0.03 * len(intervals)) * (0.4 + 0.6 * consistency)
        evidence_refs = [f"block:{b.id}" for b in speech_led]
        patterns.append(
            ClockPattern(
                pattern_type="presenter_return_interval",
                description=f"the presenter returns roughly every {mean_interval / 60:.1f} "
                f"minutes (median {statistics.median(intervals) / 60:.1f} min)",
                occurrences=len(speech_led),
                evidence=[isoformat(b.start) for b in (speech_led[:3] + speech_led[-3:])],
                confidence=confidence,
                confidence_basis=(
                    f"Calculated from {len(intervals)} speech-led content block "
                    f"intervals (consistency {consistency:.2f})"
                ),
                evidence_refs=evidence_refs[:10],
                limitations=["Measures block-to-block cadence, not individual utterance intervals"],
                sufficiency_status=clock_suff.sufficiency_status,
                sample_seconds=total_sample_seconds,
                expected_window_seconds=clock_suff.expected_window_seconds,
                coverage_ratio=clock_suff.coverage_ratio,
            )
        )

    speech = sorted((s for s in segments if s.kind == "speech"), key=lambda s: s.start)
    bucket_counts: Counter[int] = Counter()
    bucket_examples: dict[int, list[str]] = defaultdict(list)
    for s in speech:
        local = s.start.astimezone(tz)
        bucket = (local.minute // SPEECH_CLUSTER_BUCKET_MINUTES) * SPEECH_CLUSTER_BUCKET_MINUTES
        bucket_counts[bucket] += 1
        bucket_examples[bucket].append(isoformat(s.start))
    if bucket_counts:
        baseline = len(speech) / (60 / SPEECH_CLUSTER_BUCKET_MINUTES)
        for bucket, count in bucket_counts.most_common(3):
            if count < max(2, baseline * 1.5):
                continue
            ratio = count / baseline if baseline else float(count)
            patterns.append(
                ClockPattern(
                    pattern_type="speech_cluster",
                    description=f"speech activity clusters near :{bucket:02d} of the hour",
                    occurrences=count,
                    evidence=sorted(bucket_examples[bucket])[:5],
                    confidence=min(0.6, 0.15 * ratio),
                    confidence_basis=f"Cluster ratio {ratio:.2f} relative to hourly average",
                    evidence_refs=[f"event:{e['id']}" for e in events[:5]],
                    limitations=[
                        "Single-station sample; cluster may reflect a specific program format"
                    ],
                    sufficiency_status=clock_suff.sufficiency_status,
                    sample_seconds=total_sample_seconds,
                    expected_window_seconds=clock_suff.expected_window_seconds,
                    coverage_ratio=clock_suff.coverage_ratio,
                )
            )

    for candidate in recurrent_candidates(chunks, events):
        starts = [parse_iso(s) for s in candidate["chunk_starts"]]
        minutes_from_landmark = [
            min(abs(m - 0), abs(m - 30), abs(m - 60))
            for m in (s.astimezone(tz).minute for s in starts)
        ]
        near_landmark = sum(1 for m in minutes_from_landmark if m <= 3)
        if starts and near_landmark / len(starts) >= 0.6:
            position = "near the top of the hour or half-hour"
        else:
            avg_minute = round(statistics.mean(s.astimezone(tz).minute for s in starts))
            position = f"near :{avg_minute:02d} of the hour"
        patterns.append(
            ClockPattern(
                pattern_type="recurrent_element_position",
                description=f"a recurrent audio element (unidentified) appears {position}",
                occurrences=candidate["occurrences"],
                evidence=candidate["chunk_starts"][:5],
                confidence=min(0.5, 0.15 * candidate["occurrences"]),
                confidence_basis=(
                    f"Exact fingerprint match across {candidate['occurrences']} chunks"
                ),
                evidence_refs=[f"fingerprint:{candidate['fingerprint_prefix']}"],
                limitations=[
                    "Acoustic fingerprint match only; semantic identity not verified",
                    "Do not label as jingle, promo, or advertisement without verification",
                ],
                sufficiency_status=clock_suff.sufficiency_status,
                sample_seconds=total_sample_seconds,
                expected_window_seconds=clock_suff.expected_window_seconds,
                coverage_ratio=clock_suff.coverage_ratio,
            )
        )
    return patterns


# ---------------------------------------------------------------------------------- dayparts
def daypart_analysis(
    segments: list[TimelineSegment],
    blocks: list[ContentBlock],
    candidates: list[ProgramCandidate],
    patterns: list[ClockPattern],
    tz: tzinfo,
    *,
    transcription_valid: bool = True,
) -> list[DaypartStat]:
    """Analyze dayparts with sample sufficiency and analytical validity gates.

    If transcription is invalid/failed:
    - speech_seconds is None (UNAVAILABLE, never 0)
    - unknown_seconds is None (never inferred as non-speech)
    - observations do not claim speech or non-speech ratios.
    If sample is INSUFFICIENT_SAMPLE:
    - observations do not make sweeping claims about the daypart.
    """
    audio = [s for s in segments if s.kind != "capture_gap"]
    totals: dict[str, dict[str, float]] = {name: defaultdict(float) for name in DAYPART_ORDER}

    for seg in audio:
        cursor = seg.start
        while cursor < seg.end:
            name, _window_start, window_end = _daypart_at(cursor, tz)
            piece_end = min(seg.end, window_end)
            totals[name][_lean(seg.kind)] += (piece_end - cursor).total_seconds()
            cursor = piece_end

    stats: list[DaypartStat] = []
    for name in DAYPART_ORDER:
        t = totals[name]
        monitored = sum(t.values())
        if monitored <= 0:
            continue

        suff = assess_daypart_sufficiency(sample_seconds=monitored)
        in_daypart_blocks = [b for b in blocks if _daypart_name(b.start, tz) == name]
        avg_block = (
            statistics.mean(b.duration_seconds for b in in_daypart_blocks)
            if in_daypart_blocks
            else None
        )
        daypart_gaps = presenter_return_intervals(in_daypart_blocks)
        presenter_interval = (
            statistics.median(daypart_gaps)
            if (transcription_valid and len(daypart_gaps) >= 2)
            else None
        )
        recurrent_here = sum(
            1
            for p in patterns
            if p.pattern_type == "recurrent_element_position"
            and any(_daypart_name(parse_iso(e), tz) == name for e in p.evidence)
        )
        candidates_here = sum(
            1
            for c in candidates
            if any(_daypart_name(parse_iso(e), tz) == name for e in c.evidence)
        )

        coverage_fraction = suff.coverage_ratio
        confidence = (
            round(min(0.8, 0.2 + 0.6 * coverage_fraction), 3) if transcription_valid else 0.0
        )

        observations: list[dict[str, Any]] = []
        limitations: list[str] = []

        if not transcription_valid:
            speech_s = None
            unknown_s = None
            silence_s = round(t.get("silence", 0.0), 1)
            limitations.append(
                "Speech and non-speech ratios are UNAVAILABLE because transcription failed."
            )
        else:
            speech_s = t.get("speech", 0.0)
            unknown_s = t.get("unknown", 0.0)
            silence_s = t.get("silence", 0.0)

            if suff.sufficiency_status == SufficiencyStatus.INSUFFICIENT_SAMPLE:
                limitations.append(
                    f"Sample covers only {monitored:.0f}s ({coverage_fraction:.1%}) "
                    f"of the 4h {name} daypart. Insufficient to characterize the broadcast format."
                )
            else:
                speech_pct = round(speech_s / monitored * 100, 1)
                observations.append(
                    {
                        "type": "OBSERVED",
                        "text": (
                            f"Speech-classified time is {speech_pct}% of monitored "
                            f"time in the {name} daypart."
                        ),
                        "confidence": confidence,
                    }
                )
                if unknown_s + silence_s > speech_s:
                    unk_sil_pct = round((unknown_s + silence_s) / monitored * 100, 1)
                    observations.append(
                        {
                            "type": "OBSERVED",
                            "text": (
                                f"Unclassified audio exceeds speech in the {name} "
                                f"daypart sample ({unk_sil_pct}%)."
                            ),
                            "confidence": confidence,
                        }
                    )

        evidence_refs = [f"block:{b.id}" for b in in_daypart_blocks]

        stats.append(
            DaypartStat(
                name=name,
                window=DAYPART_LABELS[name],
                monitored_seconds=monitored,
                speech_seconds=speech_s,
                unknown_seconds=unknown_s,
                silence_seconds=silence_s,
                avg_block_duration_seconds=avg_block,
                presenter_return_interval_seconds=presenter_interval,
                recurrent_element_count=recurrent_here,
                program_candidate_count=candidates_here,
                confidence=confidence,
                confidence_basis=(
                    f"Measured across {monitored:.0f}s of captured audio in {DAYPART_LABELS[name]}"
                ),
                evidence_refs=evidence_refs[:10],
                limitations=limitations,
                sufficiency_status=suff.sufficiency_status,
                sample_seconds=suff.sample_seconds,
                expected_window_seconds=suff.expected_window_seconds,
                coverage_ratio=suff.coverage_ratio,
                sessions_count=suff.sessions_count,
                days_count=suff.days_count,
                observations=observations,
            )
        )
    return stats


# ------------------------------------------------------------------------------- diagnostics
def programming_diagnostics(
    segments: list[TimelineSegment],
    blocks: list[ContentBlock],
    candidates: list[ProgramCandidate],
    patterns: list[ClockPattern],
    *,
    transcription_valid: bool = True,
    total_captured_seconds: float = 0.0,
    failures_count: int = 0,
) -> list[Insight]:
    """Generate programming diagnostics with strict evidence classification."""
    insights: list[Insight] = []
    audio = [s for s in segments if s.kind != "capture_gap"]
    covered = sum(s.duration_seconds for s in audio)

    # HARD GATE: If transcription failed/unavailable, strictly REFUSE any speech or format claims
    if not transcription_valid:
        insights.append(
            Insight(
                classification=EvidenceClass.OBSERVED,
                type="OBSERVED",
                observation=(
                    f"Programming analysis is BLOCKED: audio capture completed "
                    f"({total_captured_seconds:.0f}s), but transcription/classification "
                    f"dependencies failed ({failures_count} failure incidents)."
                ),
                evidence=f"{failures_count} transcription failure incident(s) recorded",
                occurrences=failures_count,
                confidence=1.0,
                confidence_basis=(
                    "Direct incident verification; technical failure is not converted "
                    "to programming evidence"
                ),
                evidence_refs=[f"block:{b.id}" for b in blocks[:5]],
                limitations=[
                    "Speech duration is UNAVAILABLE (not zero)",
                    "Non-speech duration is UNAVAILABLE (not 100%)",
                    (
                        "No clock patterns, program candidates, or planning inputs can "
                        "be inferred without verified evidence"
                    ),
                ],
                nas_fm_planning_input=None,
            )
        )
        return insights

    speech_total = sum(s.duration_seconds for s in audio if s.kind == "speech")
    speech_segments = [s for s in audio if s.kind == "speech"]
    if covered > 0 and speech_segments:
        insights.append(
            Insight(
                classification=EvidenceClass.OBSERVED,
                type="OBSERVED",
                observation=(
                    f"Speech-classified time is {speech_total / covered * 100:.1f}% "
                    f"of the {covered:.0f}s of covered audio."
                ),
                evidence=f"{len(speech_segments)} speech segment(s)",
                occurrences=len(speech_segments),
                confidence=1.0,
                confidence_basis="Direct duration sum of verified Whisper speech segments",
                evidence_refs=[f"block:{b.id}" for b in blocks if b.block_type == "speech-heavy"][
                    :5
                ],
                limitations=[
                    (
                        "Applies only to monitored duration; "
                        "not an average for the full station schedule"
                    )
                ],
            )
        )

    for pattern in patterns:
        if pattern.confidence < 0.2:
            continue
        if pattern.pattern_type == "presenter_return_interval":
            insights.append(
                Insight(
                    classification=EvidenceClass.INFERRED,
                    type="INFERRED",
                    observation=(
                        f"Presenter interventions recur: {pattern.description}, "
                        f"suggesting structured short segments."
                    ),
                    evidence=f"{pattern.occurrences} speech-led content block(s)",
                    occurrences=pattern.occurrences,
                    confidence=pattern.confidence,
                    confidence_basis=pattern.confidence_basis,
                    evidence_refs=pattern.evidence_refs,
                    limitations=pattern.limitations,
                    nas_fm_planning_input=(
                        "Consider testing comparably short presenter interventions separated "
                        "by non-speech material in the matching NAS FM daypart, then compare "
                        "audience response - do not copy the observed cadence directly."
                    ),
                )
            )
        elif pattern.pattern_type == "speech_cluster":
            insights.append(
                Insight(
                    classification=EvidenceClass.INFERRED,
                    type="INFERRED",
                    observation=(
                        f"Speech blocks cluster around a specific minute position: "
                        f"{pattern.description}."
                    ),
                    evidence=f"{pattern.occurrences} occurrence(s) at this minute bucket",
                    occurrences=pattern.occurrences,
                    confidence=pattern.confidence,
                    confidence_basis=pattern.confidence_basis,
                    evidence_refs=pattern.evidence_refs,
                    limitations=pattern.limitations,
                    nas_fm_planning_input=(
                        "A comparable minute-position cue could be tested as a "
                        "listener-expectation anchor in the NAS FM clock."
                    ),
                )
            )
        elif pattern.pattern_type == "recurrent_element_position":
            insights.append(
                Insight(
                    classification=EvidenceClass.INFERRED,
                    type="INFERRED",
                    observation=(
                        f"A recurrent element appears close to a clock landmark: "
                        f"{pattern.description}."
                    ),
                    evidence=(
                        f"{pattern.occurrences} fingerprint occurrence(s) "
                        f"(acoustic fingerprint, identity unverified)"
                    ),
                    occurrences=pattern.occurrences,
                    confidence=pattern.confidence,
                    confidence_basis=pattern.confidence_basis,
                    evidence_refs=pattern.evidence_refs,
                    limitations=pattern.limitations,
                    nas_fm_planning_input=(
                        "Investigate this recurring element as a possible station-imaging "
                        "or transition cue before drawing conclusions; it is not identified "
                        "as a jingle, promo, or advertisement."
                    ),
                )
            )

    for candidate in candidates:
        if candidate.confidence < 0.3:
            continue
        bucket_label = candidate.to_dict()["bucket"]
        insights.append(
            Insight(
                classification=EvidenceClass.INFERRED,
                type="INFERRED",
                observation=(
                    f"A {candidate.block_type} block recurs near {bucket_label} of the hour "
                    f"across {candidate.occurrences} monitored hours "
                    f"(mean duration {candidate.mean_duration_seconds:.0f}s)."
                ),
                evidence=f"{candidate.occurrences} occurrence(s)",
                occurrences=candidate.occurrences,
                confidence=candidate.confidence,
                confidence_basis=candidate.confidence_basis,
                evidence_refs=candidate.evidence_refs,
                limitations=candidate.limitations,
                nas_fm_planning_input=(
                    "Treat as a program-candidate hypothesis only; verify over additional "
                    "monitoring sessions before basing any schedule decision on it."
                ),
            )
        )

    unknown_blocks = [b for b in blocks if b.block_type == "unknown" and b.duration_seconds >= 300]
    if unknown_blocks:
        insights.append(
            Insight(
                classification=EvidenceClass.OBSERVED,
                type="OBSERVED",
                observation=(
                    f"Long unclassified audio sequences occur: {len(unknown_blocks)} "
                    f"block(s) of at least 5 minutes each, totaling "
                    f"{sum(b.duration_seconds for b in unknown_blocks):.0f}s."
                ),
                evidence=f"{len(unknown_blocks)} unclassified block(s) >= 300s",
                occurrences=len(unknown_blocks),
                confidence=1.0,
                confidence_basis="Direct duration measurement of unclassified timeline blocks",
                evidence_refs=[f"block:{b.id}" for b in unknown_blocks[:5]],
                limitations=["Unclassified audio is not verified as music, silence, or noise"],
            )
        )

    inferred_with_input = [
        i
        for i in insights
        if i.classification == EvidenceClass.INFERRED and i.nas_fm_planning_input
    ]
    if len(inferred_with_input) >= 2:
        insights.append(
            Insight(
                classification=EvidenceClass.NAS_FM_PLANNING_INPUT,
                type="RECOMMENDATION",
                observation=(
                    "Use the INFERRED hypotheses above for NAS FM's own programming experiments."
                ),
                evidence=f"{len(inferred_with_input)} INFERRED finding(s) with planning input",
                occurrences=len(inferred_with_input),
                confidence=0.5,
                confidence_basis="Derived from multiple INFERRED broadcast observations",
                evidence_refs=[r for i in inferred_with_input for r in i.evidence_refs[:2]],
                limitations=[
                    "None should be treated as proof without multi-day monitoring; "
                    "a single session is a hypothesis, not a final schedule conclusion."
                ],
                nas_fm_planning_input=(
                    "None should be treated as proof without multi-day monitoring "
                    "across more sessions; a single session is a starting hypothesis, "
                    "not a conclusion."
                ),
            )
        )
    return insights


# ------------------------------------------------------------------------------------- top level
def build_programming_analysis(
    db: Database,
    session_id: str,
    *,
    expects_transcription: bool = True,
    is_dependency_unavailable: bool = False,
) -> dict[str, Any]:
    """Pure, deterministic read of database state with strict analytical validity gates."""
    session = db.session(session_id)
    if not session:
        raise KeyError(f"unknown session: {session_id}")
    chunks = db.chunks(session_id)
    events = db.events(session_id)
    incidents = db.incidents(session_id)
    tz = station_timezone(session.get("timezone"))

    segments = build_timeline(chunks, events)
    coverage_metrics = compute_timeline_coverage(chunks, segments)
    tz_meta = timezone_metadata(tz)

    captured = coverage_metrics.captured_seconds
    speech_events = [e for e in events if e.get("kind") == "speech" and e.get("text")]
    transcription_failures = sum(
        1 for i in incidents if i["kind"] in ("analysis_failure", "transcription_incident")
    )
    critical_incidents = sum(
        1 for i in incidents if i["kind"] in ("capture_error", "monitor_error")
    )

    confs = [float(e["confidence"]) for e in speech_events if e.get("confidence") is not None]
    mean_conf = sum(confs) / len(confs) if confs else None

    # Evaluate analytical validity
    target_s = float(session.get("target_seconds", 600.0))
    validity = evaluate_analytical_validity(
        has_chunks=bool(chunks),
        captured_seconds=captured,
        expected_seconds=target_s,
        chunk_count=len(chunks),
        unanalyzed_chunks=sum(1 for c in chunks if not c["analyzed"]),
        expects_transcription=expects_transcription,
        transcription_failures=transcription_failures,
        speech_events_count=len(speech_events),
        mean_speech_confidence=mean_conf,
        timeline_coverage_percent=coverage_metrics.coverage_percent,
        critical_incidents_count=critical_incidents,
        stopped_early=(session.get("status") == "stopped"),
        is_dependency_unavailable=is_dependency_unavailable,
        is_insufficient_sample=(captured < 3600.0),
    )

    # Determine whether transcription was valid
    transcription_valid = (
        validity.transcription_status == TranscriptionStatus.COMPLETED
        and validity.classification_status
        in (ClassificationStatus.COMPLETED, ClassificationStatus.LIMITED)
        and validity.programming_analysis_status != ProgrammingAnalysisStatus.BLOCKED
    )

    blocks = content_blocks(chunks, events)
    candidates = (
        program_candidates(blocks, tz, total_sample_seconds=captured) if transcription_valid else []
    )
    patterns = (
        clock_patterns(events, chunks, blocks, segments, tz, total_sample_seconds=captured)
        if transcription_valid
        else []
    )
    dayparts = daypart_analysis(
        segments, blocks, candidates, patterns, tz, transcription_valid=transcription_valid
    )
    insights = programming_diagnostics(
        segments,
        blocks,
        candidates,
        patterns,
        transcription_valid=transcription_valid,
        total_captured_seconds=captured,
        failures_count=transcription_failures,
    )
    recurrent = recurrent_candidates(chunks, events)

    duration_seconds = (
        (parse_iso(session["ended_at"]) - parse_iso(session["started_at"])).total_seconds()
        if session.get("ended_at")
        else None
    )

    return {
        "schema_version": 1,
        "session_id": session_id,
        "station": session["station_name"],
        "timezone": tz_meta["timezone"],
        "timezone_offset": tz_meta["offset_iso"],
        "validity": validity.to_dict(),
        "status": {
            "capture_status": validity.capture_status.value,
            "processing_status": validity.processing_status.value,
            "transcription_status": validity.transcription_status.value,
            "classification_status": validity.classification_status.value,
            "programming_analysis_status": validity.programming_analysis_status.value,
            "decision_readiness": validity.decision_readiness.value,
            "reasons": validity.reasons,
        },
        "content_blocks": [b.to_dict() for b in blocks],
        "program_candidates": [c.to_dict() for c in candidates],
        "clock_patterns": [p.to_dict() for p in patterns],
        "dayparts": [d.to_dict() for d in dayparts],
        "insights": [i.to_dict() for i in insights],
        "programming_intelligence": [i.to_dict() for i in insights],
        "recurrent_elements": recurrent,
        "coverage_metrics": coverage_metrics.to_dict(),
        "study_summary": {
            "duration_seconds": (
                round(duration_seconds, 1) if duration_seconds is not None else None
            ),
            "timeline_coverage_pct": coverage_metrics.coverage_percent,
            "content_block_count": len(blocks),
            "program_candidate_count": len(candidates),
            "clock_pattern_count": len(patterns),
            "dayparts_covered": len(dayparts),
            "incidents": len(incidents),
        },
    }
