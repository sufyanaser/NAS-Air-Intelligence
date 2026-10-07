"""Detached background execution plus status/stop/result inspection."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..db import Database
from ..util import parse_duration, parse_iso, utc_now
from .quality import incident_severity
from .states import AgentState
from .store import AgentStore
from .timeline import build_timeline, current_material, format_timeline, station_timezone

MODES = {"smoke": "10m", "validation": "2h"}
_WINDOWS_FLAGS = {
    "detached": 0x00000008,
    "new_group": 0x00000200,
    "no_window": 0x08000000,
    "breakaway": 0x01000000,
}


def resolve_duration(mode: str | None, duration: str | None) -> tuple[str, float]:
    """Return (mode label, seconds). An explicit duration wins over the mode default."""
    if mode in MODES and not duration:
        duration = MODES[mode]
    if not duration:
        raise ValueError("provide --duration (for example 15m, 2h) or --mode smoke|validation")
    seconds = parse_duration(duration)
    if seconds > 6 * 3600:
        raise ValueError("durations over 6h are disabled until 2h validation proves stability")
    label = mode or ("smoke" if seconds <= 900 else "validation" if seconds == 7200 else "custom")
    return label, seconds


def spawn_worker(
    run_id: str,
    *,
    db_path: str,
    storage: str,
    log_dir: Path,
    recover: bool = False,
    extra: list[str] | None = None,
) -> int:
    """Start the worker as a detached process that outlives this terminal."""
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = (log_dir / f"agent-{run_id[:8]}-{stamp}.out.log").open("ab")
    err = (log_dir / f"agent-{run_id[:8]}-{stamp}.err.log").open("ab")
    if getattr(sys, "frozen", False):
        # A frozen sidecar *is* the interpreter: there is no separate python.exe to run
        # "-m nas_air_intelligence.cli" against. The frozen entry point (desktop/sidecar/
        # entry.py) dispatches "cli" as its first argument to the regular CLI parser and
        # everything else to its own sidecar flags, so that sentinel is required here.
        command = [
            sys.executable, "cli", "--db", db_path, "agent", "_run", run_id, "--storage", storage,
        ]  # fmt: skip
    else:
        command = [
            sys.executable, "-m", "nas_air_intelligence.cli", "--db", db_path,
            "agent", "_run", run_id, "--storage", storage,
        ]  # fmt: skip
    if recover:
        command.append("--recover")
    command += extra or []
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL, "stdout": out, "stderr": err, "close_fds": True,
        "cwd": os.getcwd(),
    }  # fmt: skip
    if sys.platform == "win32":
        base = (
            _WINDOWS_FLAGS["detached"] | _WINDOWS_FLAGS["new_group"] | _WINDOWS_FLAGS["no_window"]
        )
        try:  # leave any job object the caller runs in, so closing it cannot kill the worker
            process = subprocess.Popen(
                command, creationflags=base | _WINDOWS_FLAGS["breakaway"], **kwargs
            )
        except OSError:
            process = subprocess.Popen(command, creationflags=base, **kwargs)
    else:
        process = subprocess.Popen(command, start_new_session=True, **kwargs)
    out.close()
    err.close()
    return process.pid


def create_and_launch_run(
    store: AgentStore,
    *,
    db_path: str,
    storage: str,
    log_dir: Path,
    station: str | None,
    page: str | None,
    url: str | None,
    mode: str | None,
    duration: str | None,
    segment_seconds: int,
    analyzer: str,
    model: str | None,
) -> dict[str, Any]:
    """Validate, persist, and launch one agent run. Shared by the CLI and the desktop sidecar."""
    if not station:
        raise ValueError("a station name is required")
    if not page and not url:
        raise ValueError("provide a station page or a stream url")
    if segment_seconds < 10:
        raise ValueError("segment_seconds must be at least 10")
    resolved_mode, seconds = resolve_duration(mode, duration)
    run_id = store.create(
        station_name=station, source_page=page, requested_url=url, mode=resolved_mode,
        duration_seconds=seconds, segment_seconds=segment_seconds, analyzer=analyzer, model=model,
    )  # fmt: skip
    pid = spawn_worker(run_id, db_path=db_path, storage=storage, log_dir=log_dir)
    store.update(run_id, pid=pid)
    return {
        "run_id": run_id, "station": station, "mode": resolved_mode,
        "duration_seconds": seconds, "worker_pid": pid,
    }  # fmt: skip


def live_metrics(db: Database, session_id: str | None) -> dict[str, Any]:
    if not session_id:
        return {}
    session = db.session(session_id)
    if not session:
        return {}
    chunks = db.chunks(session_id)
    incidents = db.incidents(session_id)
    started = parse_iso(session["started_at"])
    end = parse_iso(session["ended_at"]) if session["ended_at"] else utc_now()
    severities: dict[str, int] = {}
    for item in incidents:
        sev = incident_severity(item)
        severities[sev] = severities.get(sev, 0) + 1
    pending = sum(1 for c in chunks if not c["analyzed"])
    return {
        "session_status": session["status"],
        "elapsed_seconds": round((end - started).total_seconds()),
        "chunks": len(chunks),
        "captured_seconds": round(sum(float(c["duration_seconds"]) for c in chunks)),
        "processed_chunks": len(chunks) - pending,
        "pending_chunks": pending,
        "incidents": severities,
    }


def resolve_session_for_study(
    store: AgentStore, db: Database, ident: str
) -> tuple[str, float, dict[str, Any] | None]:
    """An agent-run id/prefix, or a bare session id: either way resolve to
    (session_id, requested_seconds, run-or-None). Shared by the CLI's and the sidecar's
    ``programming``/``export`` surfaces, which study data by session regardless of whether
    it was captured through the agent or the plain ``monitor`` command."""
    run = store.find(ident)
    if run:
        if not run.get("session_id"):
            raise KeyError(f"agent run {ident} has no session yet (it has not started capturing)")
        return run["session_id"], float(run["duration_seconds"]), run
    session = db.session(ident)
    if not session:
        raise KeyError(f"unknown agent run or session: {ident}")
    return ident, float(session["target_seconds"]), None


def live_timeline(db: Database, session_id: str | None) -> dict[str, Any]:
    """Timeline segments plus "Current Material" for an in-progress or finished session."""
    if not session_id:
        return {"segments": [], "rendered": [], "current_material": None}
    session = db.session(session_id)
    if not session:
        return {"segments": [], "rendered": [], "current_material": None}
    chunks = db.chunks(session_id)
    events = db.events(session_id)
    tz = station_timezone(session.get("timezone"))
    segments = build_timeline(chunks, events)
    return {
        "segments": [s.to_dict() for s in segments],
        "rendered": format_timeline(segments, tz),
        "current_material": current_material(chunks, events),
    }


def describe(db: Database, store: AgentStore, run: dict[str, Any]) -> dict[str, Any]:
    info = {
        "run_id": run["id"],
        "station": run["station_name"],
        "state": run["state"],
        "mode": run["mode"],
        "requested_seconds": run["duration_seconds"],
        "session_id": run["session_id"],
        "worker_alive": run["worker_alive"],
        "pid": run["pid"],
        "stop_requested": bool(run["stop_requested"]),
        "warnings": run["warnings"],
        "error": run["error"],
        **live_metrics(db, run["session_id"]),
    }
    if not run["is_terminal"] and not run["worker_alive"] and run["state"] != "CREATED":
        info["attention"] = (
            "worker is not reporting; run `nas-air agent stop <id>` to recover and report "
            "what was captured"
        )
    if run["result"]:
        info["result"] = run["result"]
    return info


def request_stop(
    db: Database, store: AgentStore, run: dict[str, Any], *, db_path: str, storage: str,
    log_dir: Path,
) -> dict[str, Any]:  # fmt: skip
    if run["is_terminal"]:
        return {"run_id": run["id"], "state": run["state"], "note": "already finished"}
    if run["worker_alive"]:
        store.request_stop(run["id"])
        return {"run_id": run["id"], "note": "stop requested; the worker will finalize and report"}
    if run["session_id"]:
        pid = spawn_worker(
            run["id"], db_path=db_path, storage=storage, log_dir=log_dir, recover=True
        )
        return {"run_id": run["id"], "note": "worker was lost; recovery worker started", "pid": pid}
    store.update(
        run["id"], state=AgentState.FAILED.value, error="worker lost before capture started"
    )
    return {"run_id": run["id"], "state": "FAILED", "note": "worker lost before capture started"}
