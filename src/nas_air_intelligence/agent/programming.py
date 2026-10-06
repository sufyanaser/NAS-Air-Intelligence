"""Phase 5 — Programming Intelligence: study-ready structures built from the timeline.

SQLite remains the source of truth; everything here is a pure, deterministic read of the
chunks/events already in the database - calling this twice on the same session produces
identical output. Every insight keeps the evidence hierarchy from
radio-monitoring-agent-context.md section 5 explicit:

    OBSERVED              - a direct measurement (a ratio, a count).
    INFERRED              - an interpretation of a recurring pattern, with evidence/occurrences.
    NAS FM PLANNING INPUT - a cautious, non-copying suggestion, only attached to an INFERRED row.

Nothing here ever names a "program", claims music/jingle/advertisement, or treats a single
session as proof of a lasting schedule pattern - see the confidence formulas, which are all
capped well below certainty and scale down for a single session with few occurrences.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any

from ..db import Database
from ..util import isoformat, parse_iso
from .timeline import TimelineSegment, build_timeline, recurrent_candidates, station_timezone

# A segment of a different lean shorter than this is folded into the surrounding content
# block instead of splitting it; at or above it, it only splits the block if its lean also
# differs from what the block has accumulated so far. See content_blocks() for the full rule.
BLOCK_SPLIT_THRESHOLD_SECONDS = 60.0
MIN_CANDIDATE_BLOCK_SECONDS = 60.0
CLOCK_BUCKET_MINUTES = 10
SPEECH_CLUSTER_BUCKET_MINUTES = 5

# Display order and labels only; the absolute boundaries for a given moment are computed by
# _daypart_at, which handles the Night wraparound past midnight explicitly rather than with
# modular-arithmetic tricks that are easy to get subtly wrong at the midnight boundary.
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
    return "unknown"  # unknown_audio, and any future ML label: never assumed to be music


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
    # Night: 22:00-02:00, spanning midnight. hour >= 22 is tonight's start; hour < 2 is the
    # tail end of a Night that started yesterday evening - both must resolve to the SAME
    # absolute window as the one actually containing dt, not "today's" Night by coincidence.
    if hour >= 22:
        return "Night", midnight + timedelta(hours=22), midnight + timedelta(days=1, hours=2)
    return "Night", midnight - timedelta(hours=2), midnight + timedelta(hours=2)


def _daypart_name(dt: datetime, tz: tzinfo) -> str:
    return _daypart_at(dt, tz)[0]


@dataclass
class ContentBlock:
    start: datetime
    end: datetime
    block_type: str
    lean_seconds: dict[str, float]
    evidence_event_ids: list[str]
    notes: str | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def confidence(self) -> float:
        total = sum(self.lean_seconds.values())
        if total <= 0:
            return 0.0
        # The duration-weighted share of the block that rests on firm (non-"unknown") evidence.
        firm = total - self.lean_seconds.get("unknown", 0.0)
        return round(max(0.0, min(1.0, firm / total)), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": isoformat(self.start),
            "end": isoformat(self.end),
            "duration_seconds": round(self.duration_seconds, 1),
            "block_type": self.block_type,
            "confidence": self.confidence,
            "evidence_event_ids": self.evidence_event_ids,
            "notes": self.notes,
        }


@dataclass
class ProgramCandidate:
    bucket_minute: int
    block_type: str
    occurrences: int
    mean_duration_seconds: float
    evidence: list[str]  # ISO start timestamps of each occurrence
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": f":{self.bucket_minute:02d}",
            "block_type": self.block_type,
            "occurrences": self.occurrences,
            "mean_duration_seconds": round(self.mean_duration_seconds, 1),
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


@dataclass
class ClockPattern:
    pattern_type: str
    description: str
    occurrences: int
    evidence: list[str]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_type": self.pattern_type,
            "description": self.description,
            "occurrences": self.occurrences,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class DaypartStat:
    name: str
    window: str
    monitored_seconds: float
    speech_seconds: float
    unknown_seconds: float
    silence_seconds: float
    avg_block_duration_seconds: float | None
    presenter_return_interval_seconds: float | None
    recurrent_element_count: int
    program_candidate_count: int
    confidence: float
    observations: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "daypart": self.name,
            "window": self.window,
            "monitored_seconds": round(self.monitored_seconds, 1),
            "speech_seconds": round(self.speech_seconds, 1),
            "unknown_seconds": round(self.unknown_seconds, 1),
            "silence_seconds": round(self.silence_seconds, 1),
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
            "observations": self.observations,
        }


@dataclass
class Insight:
    type: str  # OBSERVED | INFERRED | RECOMMENDATION
    observation: str
    evidence: str
    occurrences: int
    confidence: float | None
    nas_fm_planning_input: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "observation": self.observation,
            "evidence": self.evidence,
            "occurrences": self.occurrences,
            "confidence": round(self.confidence, 3) if self.confidence is not None else None,
            "nas_fm_planning_input": self.nas_fm_planning_input,
        }


# ------------------------------------------------------------------------------ content blocks
def content_blocks(
    chunks: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[ContentBlock]:
    """Group adjacent timeline segments into larger, honestly-typed blocks.

    A new block starts only when a segment of a different lean is itself substantial
    (>= BLOCK_SPLIT_THRESHOLD_SECONDS - a sustained run, not a blip) AND that lean does not
    already match the block accumulated so far; anything shorter is folded in regardless of
    lean, since a brief pause or a brief burst does not change what the block mostly is. Two
    segments that are each too short to force a split, but of different leans, legitimately
    produce a "mixed" block - that classification comes from the final duration-weighted
    fractions below, not from a separate code path. A real capture gap always ends a block
    outright: it means the recorder was not even running, which is not programming content.
    """
    segments = build_timeline(chunks, events)
    chunk_window: dict[str, tuple[datetime, datetime]] = {
        c["id"]: (
            parse_iso(c["started_at"]),
            parse_iso(c["started_at"]) + timedelta(seconds=float(c["duration_seconds"])),
        )
        for c in chunks
    }

    def _event_ids_in(start: datetime, end: datetime) -> list[str]:
        ids = []
        for event in events:
            window = chunk_window.get(event.get("chunk_id"))
            if not window:
                continue
            c_start, _c_end = window
            e_start = c_start + timedelta(seconds=float(event["start_offset"]))
            e_end = c_start + timedelta(seconds=float(event["end_offset"]))
            if e_start < end and e_end > start:
                ids.append(event["id"])
        return ids

    blocks: list[ContentBlock] = []
    members: list[TimelineSegment] = []

    def _flush() -> None:
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
        dominant = max(totals, key=lambda k: totals[k]) if totals else "unknown"
        folded = [s for s in members if _lean(s.kind) != dominant]
        notes = None
        if folded:
            folded_seconds = sum(s.duration_seconds for s in folded)
            notes = f"includes {len(folded)} short interruption(s) totaling {folded_seconds:.1f}s"
        blocks.append(
            ContentBlock(
                start=start, end=end, block_type=block_type, lean_seconds=dict(totals),
                evidence_event_ids=_event_ids_in(start, end), notes=notes,
            )  # fmt: skip
        )

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
def program_candidates(blocks: list[ContentBlock], tz: tzinfo) -> list[ProgramCandidate]:
    """Recurring, similarly-timed, similarly-typed blocks. Never a claimed program name."""
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
    for (block_type, bucket), members in groups.items():
        hours = {m.start.astimezone(tz).hour for m in members}
        if len(members) < 2 or len(hours) < 2:
            continue  # recurrence requires more than one occurrence in more than one hour
        durations = [m.duration_seconds for m in members]
        mean_duration = statistics.mean(durations)
        stdev = statistics.pstdev(durations) if len(durations) > 1 else 0.0
        consistency = max(0.0, 1.0 - (stdev / mean_duration if mean_duration else 1.0))
        confidence = min(0.9, 0.3 + 0.15 * (len(members) - 1)) * (0.5 + 0.5 * consistency)
        candidates.append(
            ProgramCandidate(
                bucket_minute=bucket, block_type=block_type, occurrences=len(members),
                mean_duration_seconds=mean_duration,
                evidence=sorted(isoformat(m.start) for m in members),
                confidence=round(confidence, 3),
            )  # fmt: skip
        )
    candidates.sort(key=lambda c: (-c.occurrences, -c.confidence))
    return candidates


# ------------------------------------------------------------------------------- clock patterns
def presenter_return_intervals(blocks: list[ContentBlock]) -> list[float]:
    """Gaps between consecutive speech-led content blocks' start times.

    Deliberately NOT computed from raw per-chunk speech segments: Whisper naturally splits
    one continuous conversation into many short segments with brief pauses between them, so
    a segment-to-segment gap measures breathing room, not how often the presenter actually
    returns after other material. A content block already folds those short pauses in.
    """
    speech_led = sorted(
        (b for b in blocks if b.block_type in {"speech-heavy", "mixed"}), key=lambda b: b.start
    )
    return [
        (b.start - a.start).total_seconds()
        for a, b in zip(speech_led, speech_led[1:], strict=False)
    ]


def clock_patterns(
    events: list[dict[str, Any]], chunks: list[dict[str, Any]], blocks: list[ContentBlock],
    segments: list[TimelineSegment], tz: tzinfo,
) -> list[ClockPattern]:  # fmt: skip
    patterns: list[ClockPattern] = []
    speech_led = sorted(
        (b for b in blocks if b.block_type in {"speech-heavy", "mixed"}), key=lambda b: b.start
    )
    intervals = presenter_return_intervals(blocks)

    if len(intervals) >= 2:
        mean_interval = statistics.mean(intervals)
        stdev_interval = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
        consistency = max(0.0, 1.0 - (stdev_interval / mean_interval if mean_interval else 1.0))
        confidence = min(0.85, 0.3 + 0.03 * len(intervals)) * (0.4 + 0.6 * consistency)
        patterns.append(
            ClockPattern(
                pattern_type="presenter_return_interval",
                description=f"the presenter returns roughly every {mean_interval / 60:.1f} "
                f"minutes (median {statistics.median(intervals) / 60:.1f} min)",
                occurrences=len(speech_led),
                evidence=[isoformat(b.start) for b in (speech_led[:3] + speech_led[-3:])],
                confidence=confidence,
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
) -> list[DaypartStat]:
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
        in_daypart_blocks = [b for b in blocks if _daypart_name(b.start, tz) == name]
        avg_block = (
            statistics.mean(b.duration_seconds for b in in_daypart_blocks)
            if in_daypart_blocks
            else None
        )
        daypart_gaps = presenter_return_intervals(in_daypart_blocks)
        presenter_interval = statistics.median(daypart_gaps) if len(daypart_gaps) >= 2 else None
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
        coverage_fraction = min(1.0, monitored / (4.0 * 3600.0))  # every daypart window is 4h
        confidence = round(min(0.8, 0.2 + 0.6 * coverage_fraction), 3)

        observations: list[dict[str, Any]] = []
        speech_s = t.get("speech", 0.0)
        unknown_s = t.get("unknown", 0.0)
        silence_s = t.get("silence", 0.0)
        if monitored >= 300:
            speech_pct = round(speech_s / monitored * 100, 1)
            observations.append(
                {
                    "type": "OBSERVED",
                    "text": f"Speech-classified time is {speech_pct}% of monitored time "
                    f"in the {name} daypart.",
                    "confidence": confidence,
                }
            )
            if unknown_s + silence_s > speech_s:
                observations.append(
                    {
                        "type": "OBSERVED",
                        "text": f"Non-speech/unknown audio dominates the {name} daypart "
                        f"({round((unknown_s + silence_s) / monitored * 100, 1)}% of "
                        "monitored time).",
                        "confidence": confidence,
                    }
                )

        stats.append(
            DaypartStat(
                name=name, window=DAYPART_LABELS[name], monitored_seconds=monitored,
                speech_seconds=speech_s, unknown_seconds=unknown_s, silence_seconds=silence_s,
                avg_block_duration_seconds=avg_block,
                presenter_return_interval_seconds=presenter_interval,
                recurrent_element_count=recurrent_here, program_candidate_count=candidates_here,
                confidence=confidence, observations=observations,
            )  # fmt: skip
        )
    return stats


# ------------------------------------------------------------------------------- diagnostics
def programming_diagnostics(
    segments: list[TimelineSegment],
    blocks: list[ContentBlock],
    candidates: list[ProgramCandidate],
    patterns: list[ClockPattern],
) -> list[Insight]:
    insights: list[Insight] = []
    audio = [s for s in segments if s.kind != "capture_gap"]
    covered = sum(s.duration_seconds for s in audio)
    speech_total = sum(s.duration_seconds for s in audio if s.kind == "speech")
    if covered > 0:
        insights.append(
            Insight(
                type="OBSERVED",
                observation=f"Speech-classified time is {speech_total / covered * 100:.1f}% "
                f"of the {covered:.0f}s of covered audio.",
                evidence=f"{sum(1 for s in audio if s.kind == 'speech')} speech segment(s)",
                occurrences=sum(1 for s in audio if s.kind == "speech"),
                confidence=1.0,
            )
        )

    for pattern in patterns:
        if pattern.confidence < 0.2:
            continue
        if pattern.pattern_type == "presenter_return_interval":
            insights.append(
                Insight(
                    type="INFERRED",
                    observation=f"Presenter interventions recur: {pattern.description}, "
                    "suggesting short, frequent segments rather than long uninterrupted blocks.",
                    evidence=f"{pattern.occurrences} speech segment(s)",
                    occurrences=pattern.occurrences,
                    confidence=pattern.confidence,
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
                    type="INFERRED",
                    observation=f"Speech blocks cluster around a specific minute position: "
                    f"{pattern.description}.",
                    evidence=f"{pattern.occurrences} occurrence(s) at this minute bucket",
                    occurrences=pattern.occurrences,
                    confidence=pattern.confidence,
                    nas_fm_planning_input=(
                        "A comparable minute-position cue (e.g. a recurring short presenter "
                        "segment) could be tested as a listener-expectation anchor in the NAS "
                        "FM clock."
                    ),
                )
            )
        elif pattern.pattern_type == "recurrent_element_position":
            insights.append(
                Insight(
                    type="INFERRED",
                    observation=f"A recurrent element appears close to a clock landmark: "
                    f"{pattern.description}.",
                    evidence=f"{pattern.occurrences} fingerprint occurrence(s) "
                    "(exact-match acoustic fingerprint, identity not verified)",
                    occurrences=pattern.occurrences,
                    confidence=pattern.confidence,
                    nas_fm_planning_input=(
                        "Investigate this recurring element as a possible station-imaging or "
                        "transition cue before drawing conclusions; it is not identified as a "
                        "jingle, promo, or advertisement, and must not be copied."
                    ),
                )
            )

    for candidate in candidates:
        if candidate.confidence < 0.3:
            continue
        bucket_label = candidate.to_dict()["bucket"]
        insights.append(
            Insight(
                type="INFERRED",
                observation=f"A {candidate.block_type} block recurs near {bucket_label} of "
                f"the hour across {candidate.occurrences} monitored hours "
                f"(mean duration {candidate.mean_duration_seconds:.0f}s).",
                evidence=f"{candidate.occurrences} occurrence(s)",
                occurrences=candidate.occurrences,
                confidence=candidate.confidence,
                nas_fm_planning_input=(
                    "Treat as a program-candidate signal only; verify over additional "
                    "monitoring sessions before basing any schedule decision on it."
                ),
            )
        )

    long_unknown_blocks = [
        b for b in blocks if b.block_type == "non-speech/unknown" and b.duration_seconds >= 300
    ]
    if long_unknown_blocks:
        insights.append(
            Insight(
                type="OBSERVED",
                observation=f"Long non-speech/unknown sequences occur: {len(long_unknown_blocks)} "
                f"block(s) of at least 5 minutes each, totaling "
                f"{sum(b.duration_seconds for b in long_unknown_blocks):.0f}s.",
                evidence="content blocks classified non-speech/unknown by duration-weighted lean",
                occurrences=len(long_unknown_blocks),
                confidence=1.0,
            )
        )

    inferred_with_input = [i for i in insights if i.type == "INFERRED" and i.nas_fm_planning_input]
    if len(inferred_with_input) >= 2:
        insights.append(
            Insight(
                type="RECOMMENDATION",
                observation="Use the INFERRED rows above as hypotheses for NAS FM's own "
                "programming experiments.",
                evidence=f"{len(inferred_with_input)} INFERRED finding(s) with a planning input",
                occurrences=len(inferred_with_input),
                confidence=None,
                nas_fm_planning_input="None should be treated as proof without multi-day "
                "monitoring across more sessions; a single session is a starting hypothesis, "
                "not a conclusion.",
            )
        )
    return insights


# ------------------------------------------------------------------------------------- top level
def build_programming_analysis(db: Database, session_id: str) -> dict[str, Any]:
    """Pure function of the database state for ``session_id``: deterministic and reproducible."""
    session = db.session(session_id)
    if not session:
        raise KeyError(f"unknown session: {session_id}")
    chunks = db.chunks(session_id)
    events = db.events(session_id)
    incidents = db.incidents(session_id)
    tz = station_timezone(session.get("timezone"))

    segments = build_timeline(chunks, events)
    blocks = content_blocks(chunks, events)
    candidates = program_candidates(blocks, tz)
    patterns = clock_patterns(events, chunks, blocks, segments, tz)
    dayparts = daypart_analysis(segments, blocks, candidates, patterns, tz)
    insights = programming_diagnostics(segments, blocks, candidates, patterns)
    recurrent = recurrent_candidates(chunks, events)

    covered = sum(s.duration_seconds for s in segments if s.kind != "capture_gap")
    captured = sum(float(c["duration_seconds"]) for c in chunks)
    duration_seconds = (
        (parse_iso(session["ended_at"]) - parse_iso(session["started_at"])).total_seconds()
        if session.get("ended_at")
        else None
    )
    return {
        "schema_version": 1,
        "session_id": session_id,
        "station": session["station_name"],
        "timezone": str(getattr(tz, "key", tz)),
        "content_blocks": [b.to_dict() for b in blocks],
        "program_candidates": [c.to_dict() for c in candidates],
        "clock_patterns": [p.to_dict() for p in patterns],
        "dayparts": [d.to_dict() for d in dayparts],
        "insights": [i.to_dict() for i in insights],
        "recurrent_elements": recurrent,
        "study_summary": {
            "duration_seconds": (
                round(duration_seconds, 1) if duration_seconds is not None else None
            ),
            "timeline_coverage_pct": round(covered / captured * 100, 1) if captured else 0.0,
            "content_block_count": len(blocks),
            "program_candidate_count": len(candidates),
            "clock_pattern_count": len(patterns),
            "dayparts_covered": len(dayparts),
            "incidents": len(incidents),
        },
    }
