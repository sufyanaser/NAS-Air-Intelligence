"""Unified wall-clock timeline built from chunk timestamps plus per-chunk event offsets.

Classification policy: every segment carries a tier. ``Detected`` is a measured fact
(silencedetect, a capture gap, transcribed speech with usable confidence), ``Likely`` is a
model output with weak support, and ``Unknown`` is everything else. Non-speech audio is
never promoted to music/jingle/ad.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any

from ..util import isoformat, parse_iso

GAP_TOLERANCE_SECONDS = 3.0
SPEECH_DETECTED_CONFIDENCE = 0.4
MIN_SEGMENT_SECONDS = 0.5
_EPS = 1e-6

# Kinds written by analyzers that mean "non-silent audio nobody classified".
_UNCLASSIFIED = {"audio", "unknown", "unknown_audio"}
_PRIORITY = {"silence": 3, "speech": 2}


@dataclass
class TimelineSegment:
    start: datetime
    end: datetime
    kind: str
    tier: str
    event_count: int = 0
    mean_confidence: float | None = None

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start"] = isoformat(self.start)
        data["end"] = isoformat(self.end)
        data["duration_seconds"] = round(self.duration_seconds, 3)
        if self.mean_confidence is not None:
            data["mean_confidence"] = round(self.mean_confidence, 3)
        return data


def normalize_kind(kind: str) -> str:
    return "unknown_audio" if kind in _UNCLASSIFIED else kind


def _tier(kind: str, confidence: float | None) -> str:
    if kind in {"silence", "capture_gap"}:
        return "Detected"
    if kind == "speech":
        if confidence is not None and confidence >= SPEECH_DETECTED_CONFIDENCE:
            return "Detected"
        return "Likely"
    if kind == "unknown_audio":
        return "Unknown"
    return "Likely"  # model-derived classes such as music/noise from an ML adapter


@dataclass
class TimelineCoverageMetrics:
    captured_seconds: float
    unique_covered_seconds: float
    gap_seconds: float
    overlap_seconds: float
    coverage_percent: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "captured_seconds": round(self.captured_seconds, 1),
            "unique_covered_seconds": round(self.unique_covered_seconds, 1),
            "gap_seconds": round(self.gap_seconds, 1),
            "overlap_seconds": round(self.overlap_seconds, 1),
            "coverage_percent": round(self.coverage_percent, 2),
        }


def compute_timeline_coverage(
    chunks: list[dict[str, Any]],
    segments: list[TimelineSegment],
) -> TimelineCoverageMetrics:
    """Calculate timeline coverage based on unique covered seconds / captured audio seconds.

    Guarantees:
    - coverage_percent never exceeds 100.0
    - separately tracks captured_seconds, unique_covered_seconds, gap_seconds, overlap_seconds
    """
    captured_seconds = sum(float(c["duration_seconds"]) for c in chunks)
    gap_seconds = sum(s.duration_seconds for s in segments if s.kind == "capture_gap")

    # Calculate chunk-level overlaps
    overlap_seconds = 0.0
    sorted_chunks = sorted(chunks, key=lambda c: c["started_at"])
    prev_chunk_end: datetime | None = None
    for c in sorted_chunks:
        c_start = parse_iso(c["started_at"])
        c_dur = float(c["duration_seconds"])
        c_end = c_start + timedelta(seconds=c_dur)
        if prev_chunk_end is not None and c_start < prev_chunk_end:
            chunk_overlap = (prev_chunk_end - c_start).total_seconds()
            overlap_seconds += max(0.0, chunk_overlap)
        prev_chunk_end = max(prev_chunk_end, c_end) if prev_chunk_end else c_end

    # Calculate unique covered audio intervals (non-gap segments)
    real_segments = [s for s in segments if s.kind != "capture_gap"]
    if not real_segments:
        unique_covered_seconds = 0.0
    else:
        intervals = sorted([(s.start, s.end) for s in real_segments], key=lambda x: x[0])
        merged: list[list[datetime]] = []
        for start, end in intervals:
            if not merged:
                merged.append([start, end])
            else:
                last_end = merged[-1][1]
                if start < last_end:
                    merged[-1][1] = max(last_end, end)
                else:
                    merged.append([start, end])
        unique_covered_seconds = sum((end - start).total_seconds() for start, end in merged)

    # Invariant: unique covered seconds cannot exceed captured seconds
    if captured_seconds > 0:
        raw_pct = (unique_covered_seconds / captured_seconds) * 100.0
        coverage_percent = min(100.0, max(0.0, raw_pct))
    else:
        coverage_percent = 0.0

    return TimelineCoverageMetrics(
        captured_seconds=captured_seconds,
        unique_covered_seconds=min(captured_seconds, unique_covered_seconds),
        gap_seconds=gap_seconds,
        overlap_seconds=overlap_seconds,
        coverage_percent=coverage_percent,
    )


def timezone_metadata(tz: tzinfo) -> dict[str, Any]:
    """Explicit timezone metadata for audit and display."""
    now = datetime.now(tz)
    offset = now.utcoffset()
    offset_seconds = int(offset.total_seconds()) if offset else 0
    h, rem = divmod(abs(offset_seconds), 3600)
    sign = "+" if offset_seconds >= 0 else "-"
    offset_iso = f"{sign}{h:02d}:{rem // 60:02d}"
    tz_name = getattr(tz, "key", str(tz))
    return {
        "timezone": tz_name,
        "offset_seconds": offset_seconds,
        "offset_iso": offset_iso,
    }


def station_timezone(name: str | None) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name or "Asia/Baghdad")
    except Exception:
        return timezone(timedelta(hours=3)) if (name == "Asia/Baghdad" or not name) else UTC


def _chunk_segments(chunk: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Elementary (kind, start, end, event ids, confidences) intervals for one chunk."""
    duration = float(chunk["duration_seconds"])
    clipped = []
    for event in events:
        start = max(0.0, min(duration, float(event["start_offset"])))
        end = max(0.0, min(duration, float(event["end_offset"])))
        if end - start > _EPS:
            clipped.append((start, end, normalize_kind(event["kind"]), event))
    points = {0.0, duration}
    for start, end, _, _ in clipped:
        points.update((start, end))
    ordered = sorted(points)
    pieces = []
    for a, b in zip(ordered, ordered[1:], strict=False):
        if b - a <= _EPS:
            continue
        mid = (a + b) / 2.0
        covering = [c for c in clipped if c[0] <= mid < c[1]]
        if covering:
            best = max(covering, key=lambda c: _PRIORITY.get(c[2], 1))
            kind = best[2]
            used = [c[3] for c in covering if c[2] == kind]
        else:
            kind, used = "unknown_audio", []
        pieces.append(
            {
                "kind": kind,
                "start": a,
                "end": b,
                "ids": {e["id"] for e in used},
                "conf": [float(e["confidence"]) for e in used if e.get("confidence") is not None],
            }
        )
    return pieces


def build_timeline(
    chunks: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[TimelineSegment]:
    by_chunk: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_chunk[event["chunk_id"]].append(event)

    raw: list[dict[str, Any]] = []
    previous_end: datetime | None = None
    for chunk in sorted(chunks, key=lambda c: c["started_at"]):
        start = parse_iso(chunk["started_at"])
        if previous_end is not None:
            gap = (start - previous_end).total_seconds()
            if gap > GAP_TOLERANCE_SECONDS:
                raw.append(
                    {
                        "kind": "capture_gap",
                        "start": previous_end,
                        "end": start,
                        "ids": set(),
                        "conf": [],
                    }  # fmt: skip
                )
            elif -GAP_TOLERANCE_SECONDS <= gap < 0:
                start = previous_end  # sub-tolerance overlap from mtime-derived timestamps
        for piece in _chunk_segments(chunk, by_chunk.get(chunk["id"], [])):
            raw.append(
                {
                    **piece,
                    "start": start + timedelta(seconds=piece["start"]),
                    "end": start + timedelta(seconds=piece["end"]),
                }
            )
        chunk_end = start + timedelta(seconds=float(chunk["duration_seconds"]))
        previous_end = max(previous_end, chunk_end) if previous_end is not None else chunk_end

    merged: list[dict[str, Any]] = []
    for piece in raw:
        last = merged[-1] if merged else None
        contiguous = (
            last is not None
            and last["kind"] == piece["kind"]
            and (piece["start"] - last["end"]).total_seconds() <= GAP_TOLERANCE_SECONDS
            and piece["kind"] != "capture_gap"
        )
        if contiguous:
            last["end"] = max(last["end"], piece["end"])
            last["ids"] |= piece["ids"]
            last["conf"].extend(piece["conf"])
        else:
            merged.append({**piece, "ids": set(piece["ids"]), "conf": list(piece["conf"])})

    # Sub-half-second slivers (chunk-boundary rounding) are absorbed by the previous segment.
    compact: list[dict[str, Any]] = []
    for item in merged:
        tiny = (item["end"] - item["start"]).total_seconds() < MIN_SEGMENT_SECONDS
        if tiny and compact and "capture_gap" not in (compact[-1]["kind"], item["kind"]):
            compact[-1]["end"] = item["end"]
        else:
            compact.append(item)

    segments = []
    for item in compact:
        mean = sum(item["conf"]) / len(item["conf"]) if item["conf"] else None
        segments.append(
            TimelineSegment(
                start=item["start"],
                end=item["end"],
                kind=item["kind"],
                tier=_tier(item["kind"], mean),
                event_count=len(item["ids"]),
                mean_confidence=mean,
            )
        )
    return segments


def format_timeline(segments: list[TimelineSegment], tz: tzinfo = UTC) -> list[str]:
    lines = []
    for seg in segments:
        a = seg.start.astimezone(tz).strftime("%H:%M:%S")
        b = seg.end.astimezone(tz).strftime("%H:%M:%S")
        lines.append(f"{a}–{b} {seg.kind} [{seg.tier}]")
    return lines


def distribution(segments: list[TimelineSegment]) -> list[dict[str, Any]]:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for seg in segments:
        if seg.kind == "capture_gap":
            continue
        totals[seg.kind] += seg.duration_seconds
        counts[seg.kind] += 1
    grand = sum(totals.values())
    return [
        {
            "kind": kind,
            "seconds": round(totals[kind], 1),
            "segments": counts[kind],
            "percent": round(totals[kind] / grand * 100.0, 2) if grand else 0.0,
        }
        for kind in sorted(totals, key=lambda k: -totals[k])
    ]


def current_material(
    chunks: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The most recently classified segment, for the desktop's "Current Material" panel.

    Per Phase2.md section 15: SPEECH / SILENCE / UNKNOWN AUDIO only - never music, jingle,
    promo, or ad. The transcript (when any) is the latest speech event inside that segment.
    """
    segments = build_timeline(chunks, events)
    real = [s for s in segments if s.kind != "capture_gap"]
    if not real:
        return None
    last = real[-1]
    chunk_start = {c["id"]: parse_iso(c["started_at"]) for c in chunks}
    text = None
    for event in sorted(events, key=lambda e: float(e.get("start_offset", 0.0)), reverse=True):
        start = chunk_start.get(event.get("chunk_id"))
        if start is None or not event.get("text"):
            continue
        offset_start = start + timedelta(seconds=float(event["start_offset"]))
        offset_end = start + timedelta(seconds=float(event["end_offset"]))
        if offset_start < last.end and offset_end > last.start:
            text = event["text"]
            break
    return {
        "kind": last.kind,
        "tier": last.tier,
        "start": isoformat(last.start),
        "end": isoformat(last.end),
        "duration_seconds": round(last.duration_seconds, 1),
        "confidence": last.mean_confidence,
        "text": text,
    }


def recurrent_candidates(
    chunks: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Identical chunk-level fingerprints seen in more than one chunk.

    This is a weak signal (exact base64 equality), reported only as a candidate: it is
    never labelled jingle/station-id/ad.
    """
    chunk_start = {c["id"]: c["started_at"] for c in chunks}
    seen: dict[str, set[str]] = defaultdict(set)
    for event in events:
        if event.get("fingerprint") and event.get("chunk_id") in chunk_start:
            seen[event["fingerprint"]].add(event["chunk_id"])
    candidates = []
    for fingerprint, chunk_ids in seen.items():
        if len(chunk_ids) >= 2:
            candidates.append(
                {
                    "label": "recurrent_audio_candidate",
                    "tier": "Unknown",
                    "fingerprint_prefix": fingerprint[:24],
                    "occurrences": len(chunk_ids),
                    "chunk_starts": sorted(chunk_start[c] for c in chunk_ids),
                }
            )
    return sorted(candidates, key=lambda c: -c["occurrences"])
