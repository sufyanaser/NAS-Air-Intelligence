from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .db import Database


def build_report(db: Database, session_id: str) -> dict[str, Any]:
    session = db.session(session_id)
    if not session:
        raise KeyError(f"unknown session: {session_id}")
    chunks = db.chunks(session_id)
    events = db.events(session_id)
    incidents = db.incidents(session_id)

    coverage_seconds = sum(float(chunk["duration_seconds"]) for chunk in chunks)
    target_seconds = float(session["target_seconds"])
    event_seconds: dict[str, float] = defaultdict(float)
    event_count: dict[str, int] = defaultdict(int)
    for event in events:
        duration = max(0.0, float(event["end_offset"]) - float(event["start_offset"]))
        event_seconds[event["kind"]] += duration
        event_count[event["kind"]] += 1

    distribution = []
    total_event_seconds = sum(event_seconds.values())
    for kind in sorted(event_seconds):
        seconds = event_seconds[kind]
        distribution.append(
            {
                "kind": kind,
                "seconds": round(seconds, 3),
                "event_count": event_count[kind],
                "percent_of_analyzed_audio": round(
                    (seconds / total_event_seconds * 100.0) if total_event_seconds else 0.0,
                    2,
                ),
            }
        )

    return {
        "schema_version": 1,
        "session": {
            "id": session["id"],
            "status": session["status"],
            "station_id": session["station_id"],
            "station_name": session["station_name"],
            "started_at": session["started_at"],
            "ended_at": session["ended_at"],
            "target_seconds": target_seconds,
        },
        "monitoring": {
            "chunk_count": len(chunks),
            "coverage_seconds": round(coverage_seconds, 3),
            "coverage_percent_of_target": round(
                min(100.0, coverage_seconds / target_seconds * 100.0) if target_seconds else 0.0,
                2,
            ),
            "incident_count": len(incidents),
        },
        "analysis": {
            "event_count": len(events),
            "distribution": distribution,
            "classification_scope": (
                "baseline silence/non-silent audio unless an optional analyzer was selected"
            ),
        },
        "incidents": incidents,
    }


def report_markdown(report: dict[str, Any]) -> str:
    session = report["session"]
    monitoring = report["monitoring"]
    analysis = report["analysis"]
    lines = [
        f"# NAS Air Intelligence — {session['station_name']}",
        "",
        f"- Session: `{session['id']}`",
        f"- Status: **{session['status']}**",
        f"- Started: {session['started_at']}",
        f"- Ended: {session['ended_at'] or 'running'}",
        f"- Captured chunks: {monitoring['chunk_count']}",
        f"- Coverage: {monitoring['coverage_percent_of_target']}% of target duration",
        f"- Stream incidents: {monitoring['incident_count']}",
        "",
        "## Baseline audio distribution",
        "",
        "| Kind | Duration (s) | Events | Share |",
        "| --- | ---: | ---: | ---: |",
    ]
    for item in analysis["distribution"]:
        lines.append(
            f"| {item['kind']} | {item['seconds']} | {item['event_count']} | "
            f"{item['percent_of_analyzed_audio']}% |"
        )
    if not analysis["distribution"]:
        lines.append("| No analyzed events yet | 0 | 0 | 0% |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This report describes the validated capture/timeline layer. It does not label "
            "speech, music, jingles, ads, or news unless an appropriate analysis adapter "
            "has actually been enabled and run.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_report_files(db: Database, session_id: str, output_dir: str | Path) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report = build_report(db, session_id)
    json_path = output / f"{session_id}.json"
    md_path = output / f"{session_id}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(report_markdown(report), encoding="utf-8")
    return json_path, md_path
