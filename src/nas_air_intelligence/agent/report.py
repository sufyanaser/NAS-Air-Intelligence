"""Structured analytical report (not a transcript dump) plus automatic self-review.

Adheres strictly to the analytical validity model:
- Summary displays separately: CAPTURE QUALITY, ANALYSIS VALIDITY, DECISION READINESS.
- When analysis did not run / failed: explicitly show UNAVAILABLE / BLOCKED / INSUFFICIENT_SAMPLE.
- Never silently substitute zero for unavailable speech or non-speech data.
- Exports validated analysis.json conforming to JSON Schema Draft 2020-12.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from .. import __version__
from ..db import Database
from ..util import isoformat, parse_iso, utc_now
from .programming import build_programming_analysis
from .quality import evaluate_gates, evaluate_session_validity, incident_severity, verdict
from .schema import validate_analysis_json
from .timeline import (
    TimelineSegment,
    build_timeline,
    compute_timeline_coverage,
    distribution,
    format_timeline,
    recurrent_candidates,
    station_timezone,
    timezone_metadata,
)
from .validity import AnalyticalValidity, DecisionReadiness, TranscriptionStatus

REQUIRED_SECTIONS = (
    "monitoring_information",
    "executive_summary",
    "summary_metrics",
    "capture_health",
    "timeline",
    "content_distribution",
    "speech_intelligence",
    "programming_observations",
    "incidents",
    "recurrent_audio_candidates",
    "confidence_limitations",
)
EXCERPT_CHARS = 100
EXCERPT_COUNT = 5
MARKDOWN_TIMELINE_LINES = 40


def _fmt_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def _is_repetitive(text: str) -> bool:
    words = text.split()
    return len(words) >= 3 and len(set(words)) / len(words) < 0.5


def _pick_excerpts(speech: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen, seen = [], set()
    ranked = sorted(
        (e for e in speech if e.get("confidence") is not None),
        key=lambda e: -float(e["confidence"]),
    )
    for event in ranked:
        text = str(event["text"]).strip()
        if text in seen or _is_repetitive(text):
            continue
        seen.add(text)
        chosen.append(event)
        if len(chosen) == EXCERPT_COUNT:
            break
    return chosen


def _language_reporting(speech: list[dict[str, Any]]) -> dict[str, Any]:
    metas = [e.get("metadata") or {} for e in speech]
    tracked = [m for m in metas if "configured_language" in m]
    if tracked:
        forced = sorted({m["configured_language"] for m in tracked if m["configured_language"]})
        if forced:
            return {
                "configured_language": forced[0] if len(forced) == 1 else forced,
                "language_detection": "not performed (language was forced in the Whisper config)",
            }
        langs = Counter(m.get("language") for m in tracked if m.get("language"))
        probs = [float(m["language_probability"]) for m in tracked if m.get("language_probability")]
        return {
            "configured_language": None,
            "language_detection": "whisper auto-detection",
            "detected_language": langs.most_common(1)[0][0] if langs else None,
            "language_probability_mean": round(sum(probs) / len(probs), 3) if probs else None,
        }
    langs = Counter(m.get("language") for m in metas if m.get("language"))
    return {
        "configured_language": None,
        "language_detection": "unknown (events predate language-provenance tracking)",
        "reported_language": langs.most_common(1)[0][0] if langs else None,
    }


def _recurrent_audio(chunks: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    fingerprints = {e["chunk_id"]: e["fingerprint"] for e in events if e.get("fingerprint")}
    return {
        "acoustic_fingerprint_primitive": "READY",
        "recurrent_audio_detection": "NOT VERIFIED",
        "chunks_with_fingerprint": len(fingerprints),
        "unique_fingerprints": len(set(fingerprints.values())),
        "exact_match_candidates": recurrent_candidates(chunks, events),
        "note": "Chunk-level fingerprints are collected but no recurrence clustering has been "
        "validated; unique fingerprints do not imply the absence of repeated audio.",
    }


def _summary_metrics(
    *,
    session: dict[str, Any],
    chunks: list[dict[str, Any]],
    segments: list[TimelineSegment],
    requested_seconds: float,
    captured: float,
    dist: list[dict[str, Any]],
    validity: AnalyticalValidity,
    coverage_metrics: Any,
) -> dict[str, Any]:
    seconds = {d["kind"]: d["seconds"] for d in dist}
    ended = parse_iso(session["ended_at"]) if session["ended_at"] else utc_now()
    actual = (ended - parse_iso(session["started_at"])).total_seconds()
    named = {"speech", "silence", "unknown_audio"}

    transcription_ok = validity.transcription_status == TranscriptionStatus.COMPLETED

    return {
        "requested_seconds": requested_seconds,
        "session_wall_clock_seconds": round(actual, 1),
        "captured_seconds": round(captured, 1),
        "chunk_count": len(chunks),
        "timeline_covered_seconds": round(coverage_metrics.unique_covered_seconds, 1),
        "timeline_coverage_of_captured": round(coverage_metrics.coverage_percent / 100.0, 4),
        "capture_gap_seconds": round(coverage_metrics.gap_seconds, 1),
        "chunk_overlap_seconds": round(coverage_metrics.overlap_seconds, 1),
        "speech_seconds": seconds.get("speech", 0.0) if transcription_ok else None,
        "silence_seconds": seconds.get("silence", 0.0),
        "unknown_seconds": seconds.get("unknown_audio", 0.0) if transcription_ok else None,
        "other_classified_seconds": round(sum(v for k, v in seconds.items() if k not in named), 1),
        "decision_readiness": validity.decision_readiness.value,
        "capture_status": validity.capture_status.value,
        "transcription_status": validity.transcription_status.value,
        "programming_analysis_status": validity.programming_analysis_status.value,
    }


def _speech_intelligence(
    events: list[dict[str, Any]],
    validity: AnalyticalValidity | None = None,
) -> dict[str, Any]:
    if validity is not None and validity.transcription_status in (
        TranscriptionStatus.FAILED,
        TranscriptionStatus.UNAVAILABLE,
    ):
        return {
            "status": "UNAVAILABLE",
            "speech_events": 0,
            "speech_seconds": None,
            "word_count": 0,
            "mean_confidence": None,
            "confidence_buckets": {"below_0.3": 0, "0.3_to_0.6": 0, "above_0.6": 0},
            "language_reporting": {"status": "UNAVAILABLE"},
            "excerpts": [],
            "note": "Speech intelligence is UNAVAILABLE because transcription dependencies failed.",
        }

    speech = [e for e in events if e["kind"] == "speech" and e.get("text")]
    confs = [float(e["confidence"]) for e in speech if e.get("confidence") is not None]
    buckets = {"below_0.3": 0, "0.3_to_0.6": 0, "above_0.6": 0}
    for c in confs:
        buckets["below_0.3" if c < 0.3 else "0.3_to_0.6" if c < 0.6 else "above_0.6"] += 1
    language_reporting = _language_reporting(speech)
    best = _pick_excerpts(speech)
    return {
        "status": "COMPLETED" if speech else "NO_SPEECH_DETECTED",
        "speech_events": len(speech),
        "speech_seconds": round(
            sum(float(e["end_offset"]) - float(e["start_offset"]) for e in speech), 1
        ),
        "word_count": sum(len(str(e["text"]).split()) for e in speech),
        "mean_confidence": round(sum(confs) / len(confs), 3) if confs else None,
        "confidence_buckets": buckets,
        "language_reporting": language_reporting,
        "excerpts": [
            {
                "confidence": round(float(e["confidence"]), 3)
                if e.get("confidence") is not None
                else None,
                "text": str(e["text"])[:EXCERPT_CHARS],
            }
            for e in best
        ],
        "note": "Short highest-confidence excerpts only; the full transcript is in the database.",
    }


def _programming_observations(
    segments: list[TimelineSegment], tz: Any, validity: AnalyticalValidity
) -> dict[str, Any]:
    if validity.programming_analysis_status.value in ("BLOCKED", "FAILED"):
        return {
            "status": "BLOCKED",
            "speech_blocks": 0,
            "silences_over_3s": 0,
            "longest_speech_block": None,
            "hourly_seconds_by_kind": {},
            "note": (
                "Programming observations are BLOCKED because "
                "transcription/classification dependencies failed."
            ),
        }

    audio = [s for s in segments if s.kind != "capture_gap"]
    speech_blocks = [s for s in audio if s.kind == "speech"]
    longest = max(speech_blocks, key=lambda s: s.duration_seconds, default=None)
    hourly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for seg in audio:
        cursor = seg.start
        while cursor < seg.end:
            local = cursor.astimezone(tz)
            hour_end = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            piece_end = min(seg.end, hour_end)
            hourly[local.strftime("%Y-%m-%d %H:00")][seg.kind] += (
                piece_end - cursor
            ).total_seconds()
            cursor = piece_end
    return {
        "status": "COMPLETED",
        "speech_blocks": len(speech_blocks),
        "silences_over_3s": sum(
            1 for s in audio if s.kind == "silence" and s.duration_seconds >= 3
        ),
        "longest_speech_block": (
            {
                "start": isoformat(longest.start),
                "seconds": round(longest.duration_seconds, 1),
            }
            if longest
            else None
        ),
        "hourly_seconds_by_kind": {
            h: {k: round(v, 1) for k, v in d.items()} for h, d in hourly.items()
        },
        "note": (
            "Structural observations only. No music, jingle, advertisement or news detector "
            "is connected, so none of those are claimed."
        ),
    }


def build_agent_report(
    db: Database,
    session_id: str,
    *,
    requested_seconds: float,
    run: dict[str, Any] | None = None,
    expects_transcription: bool | None = None,
    is_dependency_unavailable: bool = False,
) -> dict[str, Any]:
    session = db.session(session_id)
    if not session:
        raise KeyError(f"unknown session: {session_id}")
    chunks = db.chunks(session_id)
    events = db.events(session_id)
    incidents = db.incidents(session_id)
    tz = station_timezone(session.get("timezone"))
    tz_meta = timezone_metadata(tz)
    segments = build_timeline(chunks, events)
    coverage_metrics = compute_timeline_coverage(chunks, segments)

    if expects_transcription is None:
        expects_transcription = any(e.get("label") == "faster-whisper" for e in events) or (
            run and run.get("analyzer") != "baseline"
        )

    gates = evaluate_gates(
        session=session,
        chunks=chunks,
        events=events,
        incidents=incidents,
        segments=segments,
        requested_seconds=requested_seconds,
        expects_transcription=expects_transcription,
    )
    outcome, warnings = verdict(gates, has_chunks=bool(chunks))

    validity = evaluate_session_validity(
        session=session,
        chunks=chunks,
        events=events,
        incidents=incidents,
        segments=segments,
        requested_seconds=requested_seconds,
        expects_transcription=expects_transcription,
        is_dependency_unavailable=is_dependency_unavailable,
    )

    dist = distribution(segments)
    speech = _speech_intelligence(events, validity)
    captured = coverage_metrics.captured_seconds
    captured_fmt = _fmt_seconds(captured)

    # Environment extraction
    model_device = Counter(
        (m.get("model"), m.get("device"), m.get("compute_type"))
        for e in events
        if (m := e.get("metadata") or {}).get("model")
    )
    if model_device:
        model, device, compute_type = model_device.most_common(1)[0][0]
    else:
        model, device, compute_type = None, None, None

    # Executive Summary separating Capture Quality, Analysis Validity, Decision Readiness
    summary_parts = [
        (
            f"Monitoring session for {session['station_name']} captured {captured_fmt} "
            f"of audio in {len(chunks)} chunks."
        ),
        (
            f"Capture Quality: {validity.capture_status.value} "
            f"({coverage_metrics.coverage_percent:.1f}% timeline coverage)."
        ),
        (
            f"Analysis Validity: Transcription={validity.transcription_status.value}, "
            f"Classification={validity.classification_status.value}, "
            f"Programming={validity.programming_analysis_status.value}."
        ),
        f"Decision Readiness: {validity.decision_readiness.value}.",
    ]
    if validity.reasons:
        summary_parts.append(f"Notice: {' '.join(validity.reasons)}")

    summary = " ".join(summary_parts)
    by_severity = Counter(incident_severity(i) for i in incidents)

    limitations = [
        "Silence and capture gaps are measured; speech is a Whisper output whose reliability "
        "follows the reported confidence.",
        "Segments labelled unknown_audio are unclassified audio that no connected model "
        "classified; they are not assumed to be music or non-speech.",
        "Recurrent-audio detection is NOT VERIFIED; nothing here is labelled jingle, "
        "promo, advertisement or music.",
        "Speech text produced over music or singing may be hallucinated by the model.",
        "Chunk wall-clock times derive from file timestamps and are accurate to seconds.",
    ]
    if validity.limitations:
        limitations.extend(validity.limitations)
    if speech["mean_confidence"] is not None and speech["mean_confidence"] < 0.3:
        limitations.append("Transcription confidence is low; treat speech text as unreliable.")

    return {
        "schema_version": 1,
        "app_version": __version__,
        "generated_at": isoformat(utc_now()),
        "outcome": outcome,
        "decision_readiness": validity.decision_readiness.value,
        "validity": validity.to_dict(),
        "monitoring_information": {
            "station": session["station_name"],
            "session_id": session_id,
            "agent_run_id": run["id"] if run else None,
            "stream_url": session["stream_url"],
            "source_page": run.get("source_page") if run else None,
            "stream": (run or {}).get("resolved"),
            "started_at": session["started_at"],
            "ended_at": session["ended_at"],
            "requested_seconds": requested_seconds,
            "timezone": tz_meta["timezone"],
            "timezone_offset": tz_meta["offset_iso"],
            "session_status": session["status"],
        },
        "environment": {
            "python": None,
            "model": model,
            "device": device,
            "compute_type": compute_type,
            "ffmpeg": True,
        },
        "executive_summary": summary,
        "summary_metrics": _summary_metrics(
            session=session,
            chunks=chunks,
            segments=segments,
            requested_seconds=requested_seconds,
            captured=captured,
            dist=dist,
            validity=validity,
            coverage_metrics=coverage_metrics,
        ),
        "capture_health": {
            "gates": gates,
            "warnings": warnings,
            "captured_seconds": round(captured, 1),
            "chunk_count": len(chunks),
            "coverage_metrics": coverage_metrics.to_dict(),
        },
        "timeline": {
            "segment_count": len(segments),
            "segments": [s.to_dict() for s in segments],
            "rendered": format_timeline(segments, tz),
            "timezone": tz_meta["timezone"],
            "timezone_offset": tz_meta["offset_iso"],
        },
        "content_distribution": dist,
        "speech_intelligence": speech,
        "programming_observations": _programming_observations(segments, tz, validity),
        "incidents": {
            "total": sum(n for sev, n in by_severity.items() if sev != "info"),
            "informational": by_severity.get("info", 0),
            "by_severity": dict(by_severity),
            "items": [
                {
                    "occurred_at": i["occurred_at"],
                    "kind": i["kind"],
                    "severity": incident_severity(i),
                    "details": (i.get("details") or "")[:300],
                }
                for i in incidents
            ],
        },
        "recurrent_audio_candidates": _recurrent_audio(chunks, events),
        "confidence_limitations": limitations,
    }


def report_markdown(report: dict[str, Any]) -> str:
    info = report["monitoring_information"]
    health = report["capture_health"]
    speech = report["speech_intelligence"]
    obs = report["programming_observations"]
    validity = report.get("validity") or {}
    readiness = report.get("decision_readiness", DecisionReadiness.NOT_READY.value)
    cov = health.get("coverage_metrics") or {}

    lines = [
        f"# Monitoring Report — {info['station']}",
        "",
        "## Executive Summary",
        report["executive_summary"],
        "",
        f"## Decision Readiness: **{readiness}**",
        f"- Capture Status: **{validity.get('capture_status', 'UNKNOWN')}**",
        f"- Processing Status: **{validity.get('processing_status', 'UNKNOWN')}**",
        f"- Transcription Status: **{validity.get('transcription_status', 'UNKNOWN')}**",
        f"- Classification Status: **{validity.get('classification_status', 'UNKNOWN')}**",
        (
            f"- Programming Analysis Status: "
            f"**{validity.get('programming_analysis_status', 'UNKNOWN')}**"
        ),
    ]
    if validity.get("reasons"):
        lines.append(f"- Readiness Issues: {'; '.join(validity['reasons'])}")
    if validity.get("limitations"):
        lines.append(f"- Active Limitations: {'; '.join(validity['limitations'])}")

    lines += [
        "",
        "## Monitoring Information",
        f"- Session: `{info['session_id']}`  (agent run: `{info['agent_run_id'] or 'n/a'}`)",
        f"- Stream: {info['stream_url']}",
        f"- Window: {info['started_at']} → {info['ended_at'] or 'running'}",
        f"- Timezone: {info['timezone']} ({info.get('timezone_offset', '+00:00')})",
        f"- Requested: {_fmt_seconds(info['requested_seconds'])}; outcome: **{report['outcome']}**",
        "",
        "## Key Metrics",
        "| Metric | Value |",
        "| --- | ---: |",
        *[f"| {k} | {v} |" for k, v in report["summary_metrics"].items()],
        "",
        "## Capture Quality",
        (
            f"- Captured Audio: {cov.get('captured_seconds', 0):.1f}s "
            f"({cov.get('chunk_count', 0)} chunks)"
        ),
        f"- Unique Covered: {cov.get('unique_covered_seconds', 0):.1f}s",
        (
            f"- Gap Duration: {cov.get('gap_seconds', 0):.1f}s; "
            f"Overlap Duration: {cov.get('overlap_seconds', 0):.1f}s"
        ),
        f"- Timeline Coverage: **{cov.get('coverage_percent', 0):.1f}%** (capped at 100%)",
        "",
        "## Quality Gates",
        "| Gate | Status | Detail |",
        "| --- | --- | --- |",
        *[f"| {g['gate']} | {g['status']} | {g['detail']} |" for g in health["gates"]],
        "",
        (
            f"## Timeline ({report['timeline']['segment_count']} segments, "
            f"{report['timeline']['timezone']})"
        ),
        "",
        "```",
    ]
    rendered = report["timeline"]["rendered"]
    lines += rendered[:MARKDOWN_TIMELINE_LINES]
    if len(rendered) > MARKDOWN_TIMELINE_LINES:
        lines.append(f"... {len(rendered) - MARKDOWN_TIMELINE_LINES} more in the JSON report")
    lines += ["```", "", "## Content Distribution", "| Kind | Seconds | Segments | Share |",
              "| --- | ---: | ---: | ---: |"]  # fmt: skip
    lines += [
        f"| {d['kind']} | {d['seconds']} | {d['segments']} | {d['percent']}% |"
        for d in report["content_distribution"]
    ] or ["| none | 0 | 0 | 0% |"]
    lines += [
        "",
        "## Speech Intelligence",
        f"- Status: **{speech.get('status', 'COMPLETED')}**",
        f"- Speech events: {speech['speech_events']} ({speech['speech_seconds']}s, "
        f"{speech['word_count']} words); mean confidence: {speech['mean_confidence']}",
        f"- Confidence buckets: {speech['confidence_buckets']}",
        f"- Language: {speech['language_reporting']}",
    ]
    lines += [f"  - ({e['confidence']}) {e['text']}" for e in speech["excerpts"]]
    lines += [
        "",
        "## Programming Observations",
        f"- Status: **{obs.get('status', 'COMPLETED')}**",
        f"- Speech blocks: {obs['speech_blocks']}; silences over 3s: {obs['silences_over_3s']}",
        f"- Longest speech block: {obs['longest_speech_block'] or 'n/a'}",
        f"- {obs['note']}",
        "",
        "## Incidents",
        f"- Total (excluding informational): {report['incidents']['total']}; "
        f"informational: {report['incidents']['informational']}",
    ]
    lines += [
        f"  - {i['occurred_at']} {i['kind']} ({i['severity']})"
        for i in report["incidents"]["items"][:15]
    ]
    lines += ["", "## Recurrent Audio"]
    rec = report["recurrent_audio_candidates"]
    lines += [
        f"- Acoustic fingerprint primitive: {rec['acoustic_fingerprint_primitive']}",
        f"- Recurrent audio detection: {rec['recurrent_audio_detection']}",
        (
            f"- Fingerprints: {rec['chunks_with_fingerprint']} collected, "
            f"{rec['unique_fingerprints']} unique"
        ),
        f"- {rec['note']}",
    ]
    lines += ["", "## Confidence / Limitations"]
    lines += [f"- {text}" for text in report["confidence_limitations"]]
    return "\n".join(lines) + "\n"


def review_report(report: dict[str, Any], markdown: str) -> list[str]:
    problems = []
    for section in REQUIRED_SECTIONS:
        value = report.get(section)
        if value in (None, "", [], {}):
            problems.append(f"section missing or empty: {section}")
    if len(markdown) < 600:
        problems.append("markdown report is too short to be useful")
    speech = report.get("speech_intelligence") or {}
    excerpt_chars = sum(len(e["text"]) for e in speech.get("excerpts", []))
    if excerpt_chars > EXCERPT_COUNT * EXCERPT_CHARS:
        problems.append("report embeds more transcript text than the excerpt policy allows")
    timeline = report.get("timeline") or {}
    health = report.get("capture_health") or {}
    if timeline.get("segment_count") == 0 and health.get("chunk_count"):
        problems.append("chunks exist but the timeline is empty")
    return problems


def build_analysis_json_export(
    report: dict[str, Any],
    programming: dict[str, Any],
) -> dict[str, Any]:
    """Construct an export document conforming strictly to the Draft 2020-12 analysis schema."""
    info = report["monitoring_information"]
    cov = report["capture_health"].get("coverage_metrics") or {}
    env = report.get("environment") or {}
    validity = programming.get("validity") or report.get("validity") or {}

    analysis_doc = {
        "schema_version": 1,
        "generator": {
            "app": "NAS Air Intelligence",
            "version": report.get("app_version", __version__),
            "generated_at": report.get("generated_at", isoformat(utc_now())),
        },
        "session": {
            "id": info["session_id"],
            "station": info["station"],
            "stream_url": info.get("stream_url"),
            "started_at": info["started_at"],
            "ended_at": info.get("ended_at"),
            "requested_seconds": info["requested_seconds"],
            "timezone": info["timezone"],
        },
        "status": {
            "capture_status": validity.get("capture_status", "UNKNOWN"),
            "processing_status": validity.get("processing_status", "UNKNOWN"),
            "transcription_status": validity.get("transcription_status", "UNKNOWN"),
            "classification_status": validity.get("classification_status", "UNKNOWN"),
            "programming_analysis_status": validity.get("programming_analysis_status", "UNKNOWN"),
            "decision_readiness": validity.get("decision_readiness", "NOT_READY"),
            "reasons": validity.get("reasons", []),
        },
        "environment": {
            "python": env.get("python"),
            "model": env.get("model"),
            "device": env.get("device"),
            "compute_type": env.get("compute_type"),
            "ffmpeg": env.get("ffmpeg", True),
        },
        "quality": {
            "gates": report["capture_health"]["gates"],
            "warnings": report["capture_health"]["warnings"],
            "limitations": report["confidence_limitations"],
        },
        "capture_metrics": {
            "captured_seconds": cov.get(
                "captured_seconds", report["capture_health"]["captured_seconds"]
            ),
            "chunk_count": cov.get("chunk_count", report["capture_health"]["chunk_count"]),
            "gap_seconds": cov.get("gap_seconds", 0.0),
            "overlap_seconds": cov.get("overlap_seconds", 0.0),
        },
        "analysis_metrics": {
            "unique_covered_seconds": cov.get("unique_covered_seconds", 0.0),
            "coverage_percent": cov.get("coverage_percent", 0.0),
            "processed_chunks": report["capture_health"]["chunk_count"],
            "pending_chunks": 0,
        },
        "timeline": {
            "segment_count": report["timeline"]["segment_count"],
            "segments": report["timeline"]["segments"],
            "timezone": report["timeline"]["timezone"],
        },
        "content_blocks": programming.get("content_blocks", []),
        "program_candidates": programming.get("program_candidates", []),
        "clock_patterns": programming.get("clock_patterns", []),
        "dayparts": programming.get("dayparts", []),
        "recurrent_elements": programming.get("recurrent_elements", []),
        "programming_intelligence": programming.get("insights", []),
        "incidents": report["incidents"],
    }
    return analysis_doc


def write_agent_report(
    db: Database,
    session_id: str,
    output_dir: str | Path,
    **kwargs: Any,
) -> tuple[dict[str, Any], Path, Path, list[str]]:
    report = build_agent_report(db, session_id, **kwargs)
    programming = build_programming_analysis(
        db,
        session_id,
        expects_transcription=kwargs.get("expects_transcription", True),
        is_dependency_unavailable=kwargs.get("is_dependency_unavailable", False),
    )
    markdown = report_markdown(report)
    problems = review_report(report, markdown)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{session_id}.agent.json"
    md_path = out / f"{session_id}.agent.md"
    report["review"] = {"ok": not problems, "problems": problems}
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")

    # Also generate, validate, and write analysis.json
    analysis_doc = build_analysis_json_export(report, programming)
    is_valid, validation_errors = validate_analysis_json(analysis_doc)
    if not is_valid:
        problems.extend([f"Schema validation error: {err}" for err in validation_errors])
    analysis_json_path = out / "analysis.json"
    analysis_json_path.write_text(
        json.dumps(analysis_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return report, json_path, md_path, problems
