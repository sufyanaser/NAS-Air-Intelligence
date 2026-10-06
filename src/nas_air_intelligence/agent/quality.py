"""Quality gates: the agent never reports success merely because a process exited."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..util import parse_iso, utc_now
from .timeline import TimelineSegment

PASS, WARN, FAIL, INFO = "pass", "warn", "fail", "info"

_WARNING_INCIDENTS = {"ffmpeg_exit", "analysis_failure", "transcription_incident"}
_CRITICAL_INCIDENTS = {"monitor_error", "capture_error"}


def incident_severity(incident: dict[str, Any]) -> str:
    kind = incident["kind"]
    details = incident.get("details") or ""
    if kind in _CRITICAL_INCIDENTS:
        return "critical"
    if kind == "ffmpeg_exit":
        # A clean exit (returncode=0) is the stream/limit ending, not a fault.
        return "info" if details.startswith("returncode=0") and "\n" not in details else "warning"
    if kind in _WARNING_INCIDENTS:
        return "warning"
    return "info"


def _gate(name: str, status: str, detail: str, **metrics: Any) -> dict[str, Any]:
    return {"gate": name, "status": status, "detail": detail, "metrics": metrics}


def evaluate_gates(
    *,
    session: dict[str, Any],
    chunks: list[dict[str, Any]],
    events: list[dict[str, Any]],
    incidents: list[dict[str, Any]],
    segments: list[TimelineSegment],
    requested_seconds: float,
    expects_transcription: bool,
) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    captured = sum(float(c["duration_seconds"]) for c in chunks)

    # CAPTURE: compare against what could have been captured so far.
    finished = session.get("ended_at") is not None
    end = parse_iso(session["ended_at"]) if finished else utc_now()
    elapsed = (end - parse_iso(session["started_at"])).total_seconds()
    expected = min(requested_seconds, elapsed) if elapsed > 0 else requested_seconds
    ratio = captured / expected if expected > 0 else 0.0
    if not chunks:
        status, detail = FAIL, "no audio chunks were captured"
    elif ratio >= 0.9:
        status, detail = PASS, f"captured {captured:.0f}s of {expected:.0f}s expected"
    elif ratio >= 0.5:
        status, detail = WARN, f"captured only {ratio:.0%} of expected duration"
    else:
        status, detail = FAIL, f"captured only {ratio:.0%} of expected duration"
    gates.append(
        _gate(
            "capture",
            status,
            detail,
            captured_seconds=round(captured, 1),
            expected_seconds=round(expected, 1),
            requested_seconds=requested_seconds,
            ratio=round(ratio, 4),
        )  # fmt: skip
    )

    # CHUNKS: count and continuity.
    gap_segments = [s for s in segments if s.kind == "capture_gap"]
    gap_seconds = sum(s.duration_seconds for s in gap_segments)
    if not chunks:
        status, detail = FAIL, "no chunks"
    elif not gap_segments:
        status, detail = PASS, f"{len(chunks)} chunks, continuous"
    elif gap_seconds / max(captured + gap_seconds, 1.0) > 0.25:
        status, detail = FAIL, f"{len(gap_segments)} gaps totalling {gap_seconds:.0f}s"
    else:
        status, detail = WARN, f"{len(gap_segments)} gaps totalling {gap_seconds:.0f}s"
    gates.append(
        _gate(
            "chunks",
            status,
            detail,
            chunk_count=len(chunks),
            gap_count=len(gap_segments),
            gap_seconds=round(gap_seconds, 1),
        )  # fmt: skip
    )

    # INCIDENTS: by severity.
    severities = Counter(incident_severity(i) for i in incidents)
    if severities["critical"]:
        status = FAIL
    elif severities["warning"]:
        status = WARN
    else:
        status = PASS
    gates.append(
        _gate(
            "incidents",
            status,
            f"{len(incidents)} incidents ({dict(severities) or 'none'})",
            total=len(incidents),
            by_severity=dict(severities),
        )  # fmt: skip
    )

    # PROCESSING: analyzed vs pending.
    pending = sum(1 for c in chunks if not c["analyzed"])
    if not chunks:
        status, detail = WARN, "nothing to process"
    elif pending == 0:
        status, detail = PASS, f"all {len(chunks)} chunks analyzed"
    elif finished:
        status, detail = WARN, f"{pending} of {len(chunks)} chunks never analyzed"
    else:
        status, detail = INFO, f"{pending} chunks pending (session still running)"
    gates.append(
        _gate("processing", status, detail, analyzed=len(chunks) - pending, pending=pending)
    )

    # TRANSCRIPTION: was real speech analyzed?
    speech = [e for e in events if e["kind"] == "speech" and e.get("text")]
    failures = sum(
        1 for i in incidents if i["kind"] in {"analysis_failure", "transcription_incident"}
    )
    confidences = [float(e["confidence"]) for e in speech if e.get("confidence") is not None]
    mean_conf = sum(confidences) / len(confidences) if confidences else None
    if not expects_transcription:
        status, detail = WARN, "speech transcription was not enabled for this session"
    elif chunks and failures >= max(1, len(chunks) // 2):
        status, detail = FAIL, f"transcription failed on {failures} chunks"
    elif failures:
        status, detail = WARN, f"transcription failed on {failures} chunks"
    elif not speech:
        status, detail = WARN, "no transcribed speech found"
    elif mean_conf is not None and mean_conf < 0.3:
        status, detail = WARN, f"transcription confidence is low (mean {mean_conf:.2f})"
    else:
        status, detail = PASS, f"{len(speech)} speech events transcribed"
    gates.append(
        _gate(
            "transcription",
            status,
            detail,
            speech_events=len(speech),
            failures=failures,
            mean_confidence=round(mean_conf, 3) if mean_conf is not None else None,
        )  # fmt: skip
    )

    # TIMELINE: can the session be reconstructed?
    covered = sum(s.duration_seconds for s in segments if s.kind != "capture_gap")
    if not segments:
        status, detail = FAIL, "timeline is empty"
    elif captured and covered / captured < 0.98:
        status, detail = WARN, f"timeline covers {covered / captured:.0%} of captured audio"
    else:
        status, detail = PASS, f"{len(segments)} segments, full coverage of captured audio"
    gates.append(_gate("timeline", status, detail, segments=len(segments),
                       covered_seconds=round(covered, 1)))  # fmt: skip
    return gates


def verdict(gates: list[dict[str, Any]], *, has_chunks: bool) -> tuple[str, list[str]]:
    """Return (outcome, warnings): COMPLETED, COMPLETED_WITH_WARNINGS or CAPTURE_FAILED."""
    warnings = [f"{g['gate']}: {g['detail']}" for g in gates if g["status"] in {WARN, FAIL}]
    if not has_chunks:
        return "CAPTURE_FAILED", warnings
    return ("COMPLETED_WITH_WARNINGS" if warnings else "COMPLETED"), warnings
