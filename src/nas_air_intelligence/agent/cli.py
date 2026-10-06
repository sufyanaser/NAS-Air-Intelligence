"""`nas-air agent ...` commands: start, status, stop, result (plus the hidden worker entry)."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from ..db import Database
from .launcher import (
    describe,
    live_metrics,
    request_stop,
    resolve_duration,
    spawn_worker,
)
from .orchestrator import MonitoringAgent
from .report import build_agent_report
from .store import AgentStore


def _db_path(args: argparse.Namespace) -> str:
    return getattr(args, "db", None) or os.getenv("NAS_AIR_DB", "data/nas-air.db")


def _storage(args: argparse.Namespace) -> str:
    return getattr(args, "storage", None) or os.getenv("NAS_AIR_STORAGE", "data")


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _find_run(store: AgentStore, ident: str) -> dict[str, Any] | None:
    run = store.get(ident) or store.by_session(ident)
    if run:
        return run
    matches = [r for r in store.all() if r["id"].startswith(ident)]
    return matches[0] if len(matches) == 1 else None


def command_start(args: argparse.Namespace) -> int:
    name = args.station or args.name
    if not name:
        raise ValueError("--station (or --name) is required")
    if not (args.page or args.url):
        raise ValueError("provide --page <station page> or --url <stream url>")
    mode, seconds = resolve_duration(args.mode, args.duration)
    if args.segment_seconds < 10:
        raise ValueError("segment_seconds must be at least 10")
    db = Database(_db_path(args))
    store = AgentStore(db)
    run_id = store.create(
        station_name=name, source_page=args.page, requested_url=args.url, mode=mode,
        duration_seconds=seconds, segment_seconds=args.segment_seconds,
        analyzer=args.analyzer, model=args.model,
    )  # fmt: skip
    storage = _storage(args)
    pid = spawn_worker(
        run_id, db_path=str(Path(_db_path(args)).resolve()), storage=str(Path(storage).resolve()),
        log_dir=Path(storage) / "logs",
    )  # fmt: skip
    store.update(run_id, pid=pid)
    _print(
        {
            "run_id": run_id,
            "station": name,
            "mode": mode,
            "duration_seconds": seconds,
            "worker_pid": pid,
            "next": f"nas-air agent status {run_id[:8]}",
        }  # fmt: skip
    )
    return 0


def command_status(args: argparse.Namespace) -> int:
    db = Database(_db_path(args))
    store = AgentStore(db)
    if args.run:
        run = _find_run(store, args.run)
        if run:
            _print(describe(db, store, run))
            return 0
        session = db.session(args.run)
        if not session:
            raise KeyError(f"unknown agent run or session: {args.run}")
        _print({"session_id": args.run, "agent_run": None, **live_metrics(db, args.run)})
        return 0
    runs = store.all()[: args.limit]
    _print([describe(db, store, r) for r in runs] or {"note": "no agent runs yet"})
    return 0


def command_stop(args: argparse.Namespace) -> int:
    db = Database(_db_path(args))
    store = AgentStore(db)
    run = _find_run(store, args.run)
    if not run:
        raise KeyError(f"unknown agent run: {args.run}")
    storage = _storage(args)
    _print(
        request_stop(
            db,
            store,
            run,
            db_path=str(Path(_db_path(args)).resolve()),
            storage=str(Path(storage).resolve()),
            log_dir=Path(storage) / "logs",
        )  # fmt: skip
    )
    return 0


def command_result(args: argparse.Namespace) -> int:
    db = Database(_db_path(args))
    store = AgentStore(db)
    run = _find_run(store, args.run)
    if run and run["is_terminal"] and run["result"]:
        _print({**describe(db, store, run), "timeline_hint": "see report_json for the timeline"})
        return 0
    if run and not run["is_terminal"]:
        _print({**describe(db, store, run), "note": "run has not finished; no final result yet"})
        return 1
    if run:  # finished without a result (stream unavailable, failed before capture)
        _print(describe(db, store, run))
        return 1
    # A plain monitor session (not started by the agent): compute the result on demand.
    session = db.session(args.run)
    if not session:
        raise KeyError(f"unknown agent run or session: {args.run}")
    report = build_agent_report(db, args.run, requested_seconds=float(session["target_seconds"]))
    _print(
        {
            "session_id": args.run,
            "outcome": report["outcome"],
            "executive_summary": report["executive_summary"],
            "gates": {g["gate"]: g["status"] for g in report["capture_health"]["gates"]},
            "distribution": report["content_distribution"],
            "timeline_head": report["timeline"]["rendered"][:15],
        }  # fmt: skip
    )
    return 0


def command_worker(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = Database(_db_path(args))
    store = AgentStore(db)
    agent = MonitoringAgent(
        db, store, args.run_id, storage_dir=_storage(args),
        ffmpeg=os.getenv("NAS_AIR_FFMPEG", "ffmpeg"),
        ffprobe=os.getenv("NAS_AIR_FFPROBE", "ffprobe"),
    )  # fmt: skip
    final = agent.run(pid=os.getpid(), recover=args.recover)
    print(final.value)
    return 0


def register(sub: Any) -> None:
    agent = sub.add_parser("agent", help="autonomous monitoring agent (one station, one session)")
    asub = agent.add_subparsers(dest="agent_command", required=True)

    start = asub.add_parser("start", help="start a background monitoring run")
    start.add_argument("--station", help="station name")
    start.add_argument("--name", help="alias of --station")
    start.add_argument("--page", help="official station page to discover the stream from")
    start.add_argument("--url", help="direct stream URL")
    start.add_argument("--mode", choices=["smoke", "validation"], help="10m or 2h preset")
    start.add_argument("--duration", help="for example 15m, 2h (overrides the mode preset)")
    start.add_argument("--segment-seconds", type=int, default=60)
    start.add_argument("--analyzer", choices=["whisper", "baseline"], default="whisper")
    start.add_argument("--model", help="Whisper model name or path")
    start.add_argument("--storage")
    start.set_defaults(func=command_start)

    status = asub.add_parser("status", help="show one run, or the latest runs")
    status.add_argument("run", nargs="?", help="agent run id (prefix ok) or monitor session id")
    status.add_argument("--limit", type=int, default=5)
    status.set_defaults(func=command_status)

    stop = asub.add_parser("stop", help="stop a run gracefully, or recover a lost worker")
    stop.add_argument("run")
    stop.add_argument("--storage")
    stop.set_defaults(func=command_stop)

    result = asub.add_parser("result", help="final outcome, gates, and report paths")
    result.add_argument("run", help="agent run id or any monitor session id")
    result.set_defaults(func=command_result)

    worker = asub.add_parser("_run")
    worker.add_argument("run_id")
    worker.add_argument("--storage")
    worker.add_argument("--recover", action="store_true")
    worker.set_defaults(func=command_worker)
