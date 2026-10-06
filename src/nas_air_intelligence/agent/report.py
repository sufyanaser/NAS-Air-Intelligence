"""Structured analytical report (not a transcript dump) plus automatic self-review."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from ..db import Database
from ..util import isoformat, utc_now
from .quality import evaluate_gates, incident_severity, verdict
from .timeline import (
    TimelineSegment,
    build_timeline,
    distribution,
    format_timeline,
    recurrent_candidates,
    station_timezone,
)

REQUIRED_SECTIONS = (
    "monitoring_information",
    "executive_summary",
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


def _speech_intelligence(events: list[dict[str, Any]]) -> dict[str, Any]:
    speech = [e for e in events if e["kind"] == "speech" and e.get("text")]
    confs = [float(e["confidence"]) for e in speech if e.get("confidence") is not None]
    buckets = {"below_0.3": 0, "0.3_to_0.6": 0, "above_0.6": 0}
    for c in confs:
        buckets["below_0.3" if c < 0.3 else "0.3_to_0.6" if c < 0.6 else "above_0.6"] += 1
    languages = Counter(
        (e.get("metadata") or {}).get("language") for e in speech if e.get("metadata")
    )
    languages.pop(None, None)
    best = sorted(
        (e for e in speech if e.get("confidence") is not None),
        key=lambda e: -float(e["confidence"]),
    )[:EXCERPT_COUNT]
    return {
        "speech_events": len(speech),
        "speech_seconds": round(
            sum(float(e["end_offset"]) - float(e["start_offset"]) for e in speech), 1
        ),
        "word_count": sum(len(str(e["text"]).split()) for e in speech),
        "mean_confidence": round(sum(confs) / len(confs), 3) if confs else None,
        "confidence_buckets": buckets,
        "detected_languages": dict(languages),
        "excerpts": [
            {"confidence": e["confidence"], "text": str(e["text"])[:EXCERPT_CHARS]} for e in best
        ],
        "note": "Short highest-confidence excerpts only; the full transcript is in the database.",
    }


def _programming_observations(segments: list[TimelineSegment], tz: Any) -> dict[str, Any]:
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
) -> dict[str, Any]:
    session = db.session(session_id)
    if not session:
        raise KeyError(f"unknown session: {session_id}")
    chunks = db.chunks(session_id)
    events = db.events(session_id)
    incidents = db.incidents(session_id)
    tz = station_timezone(session.get("timezone"))
    segments = build_timeline(chunks, events)
    if expects_transcription is None:
        expects_transcription = any(e.get("label") == "faster-whisper" for e in events)
    gates = evaluate_gates(
        session=session, chunks=chunks, events=events, incidents=incidents, segments=segments,
        requested_seconds=requested_seconds, expects_transcription=expects_transcription,
    )  # fmt: skip
    outcome, warnings = verdict(gates, has_chunks=bool(chunks))
    dist = distribution(segments)
    speech = _speech_intelligence(events)
    captured = sum(float(c["duration_seconds"]) for c in chunks)
    top = dist[0] if dist else None
    summary = (
        f"{session['station_name']} was monitored from {session['started_at']}; "
        f"{_fmt_seconds(captured)} of audio was captured in {len(chunks)} chunks "
        f"({gates[0]['metrics']['ratio']:.0%} of the expected duration). "
        + (
            f"The largest share of classified time is {top['kind']} ({top['percent']}%). "
            if top
            else "No analyzed audio is available yet. "
        )
        + f"Outcome: {outcome}."
        + (f" Warnings: {len(warnings)}." if warnings else "")
    )
    by_severity = Counter(incident_severity(i) for i in incidents)
    return {
        "schema_version": 1,
        "generated_at": isoformat(utc_now()),
        "outcome": outcome,
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
            "timezone": session.get("timezone"),
            "session_status": session["status"],
        },
        "executive_summary": summary,
        "capture_health": {
            "gates": gates,
            "warnings": warnings,
            "captured_seconds": round(captured, 1),
            "chunk_count": len(chunks),
        },
        "timeline": {
            "segment_count": len(segments),
            "segments": [s.to_dict() for s in segments],
            "rendered": format_timeline(segments, tz),
            "timezone": str(getattr(tz, "key", tz)),
        },
        "content_distribution": dist,
        "speech_intelligence": speech,
        "programming_observations": _programming_observations(segments, tz),
        "incidents": {
            "total": len(incidents),
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
        "recurrent_audio_candidates": recurrent_candidates(chunks, events),
        "confidence_limitations": [
            "Silence and capture gaps are measured; speech is a Whisper output whose reliability "
            "follows the reported confidence.",
            "Segments labelled unknown_audio are non-silent audio that no connected model "
            "classified; they are not assumed to be music.",
            "Recurrent audio candidates are exact chunk-fingerprint matches, not confirmed "
            "jingles or advertisements.",
            "Chunk wall-clock times derive from file modification times and are accurate to "
            "about a few seconds.",
        ]
        + (
            ["Transcription confidence is low; treat speech text as unreliable."]
            if speech["mean_confidence"] is not None and speech["mean_confidence"] < 0.3
            else []
        ),
    }


def report_markdown(report: dict[str, Any]) -> str:
    info = report["monitoring_information"]
    health = report["capture_health"]
    speech = report["speech_intelligence"]
    obs = report["programming_observations"]
    lines = [
        f"# Monitoring Report — {info['station']}",
        "",
        "## Monitoring Information",
        f"- Session: `{info['session_id']}`  (agent run: `{info['agent_run_id'] or 'n/a'}`)",
        f"- Stream: {info['stream_url']}",
        f"- Window: {info['started_at']} → {info['ended_at'] or 'running'}",
        f"- Requested: {_fmt_seconds(info['requested_seconds'])}; outcome: **{report['outcome']}**",
        "",
        "## Executive Summary",
        report["executive_summary"],
        "",
        "## Capture Health",
        "| Gate | Status | Detail |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {g['gate']} | {g['status']} | {g['detail']} |" for g in health["gates"]]
    lines += [
        "",
        f"## Timeline ({report['timeline']['segment_count']} segments, "
        f"{report['timeline']['timezone']})",
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
        f"- Speech events: {speech['speech_events']} ({speech['speech_seconds']}s, "
        f"{speech['word_count']} words); mean confidence: {speech['mean_confidence']}",
        f"- Confidence buckets: {speech['confidence_buckets']}",
        f"- Languages reported by the model: {speech['detected_languages'] or 'n/a'}",
    ]
    lines += [f"  - ({e['confidence']}) {e['text']}" for e in speech["excerpts"]]
    lines += [
        "",
        "## Programming Observations",
        f"- Speech blocks: {obs['speech_blocks']}; silences over 3s: {obs['silences_over_3s']}",
        f"- Longest speech block: {obs['longest_speech_block'] or 'n/a'}",
        f"- {obs['note']}",
        "",
        "## Incidents",
        f"- Total: {report['incidents']['total']} {report['incidents']['by_severity']}",
    ]
    lines += [
        f"  - {i['occurred_at']} {i['kind']} ({i['severity']})"
        for i in report["incidents"]["items"][:15]
    ]
    lines += ["", "## Recurrent Audio Candidates"]
    cands = report["recurrent_audio_candidates"]
    lines += [
        f"- {c['label']} x{c['occurrences']} (fp {c['fingerprint_prefix']}…)" for c in cands[:10]
    ] or ["- none found"]
    lines += ["", "## Confidence / Limitations"]
    lines += [f"- {text}" for text in report["confidence_limitations"]]
    return "\n".join(lines) + "\n"


def review_report(report: dict[str, Any], markdown: str) -> list[str]:
    """Automatic pre-acceptance review. Returns problems (empty list means acceptable)."""
    problems = []
    for section in REQUIRED_SECTIONS:
        value = report.get(section)
        # an empty list is legitimate only for recurrent candidates
        empty = value in (None, "", [], {})
        if empty and (section != "recurrent_audio_candidates" or value is None):
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


def write_agent_report(
    db: Database,
    session_id: str,
    output_dir: str | Path,
    **kwargs: Any,
) -> tuple[dict[str, Any], Path, Path, list[str]]:
    report = build_agent_report(db, session_id, **kwargs)
    markdown = report_markdown(report)
    problems = review_report(report, markdown)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{session_id}.agent.json"
    md_path = out / f"{session_id}.agent.md"
    report["review"] = {"ok": not problems, "problems": problems}
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    return report, json_path, md_path, problems
