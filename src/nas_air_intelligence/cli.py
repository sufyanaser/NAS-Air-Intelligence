from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .agent.cli import register as register_agent
from .analysis import (
    FfmpegSilenceAnalyzer,
    InaSpeechMusicAnalyzer,
    WhisperSpeechAnalyzer,
    analyze_pending_chunks,
)
from .db import Database
from .ffmpeg import ToolMissingError
from .recorder import StreamMonitor
from .reporting import build_report, write_report_files
from .util import parse_duration


def _db_path(args: argparse.Namespace) -> str:
    return getattr(args, "db", None) or os.getenv("NAS_AIR_DB", "data/nas-air.db")


def _storage_path(args: argparse.Namespace) -> str:
    return getattr(args, "storage", None) or os.getenv("NAS_AIR_STORAGE", "data")


def _analyzer(name: str, ffmpeg: str):
    if name == "none":
        return None
    if name == "baseline":
        return FfmpegSilenceAnalyzer(ffmpeg=ffmpeg)
    if name == "whisper":
        return WhisperSpeechAnalyzer(ffmpeg=ffmpeg)
    if name == "ina":
        return InaSpeechMusicAnalyzer()
    raise ValueError(f"unknown analyzer: {name}")


def command_doctor(args: argparse.Namespace) -> int:
    cuda_available = False
    try:
        import ctranslate2

        cuda_available = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass

    payload = {
        "python": sys.version.split()[0],
        "ffmpeg": shutil.which(args.ffmpeg),
        "ffprobe": shutil.which(args.ffprobe),
        "database": str(Path(_db_path(args)).resolve()),
        "storage": str(Path(_storage_path(args)).resolve()),
        "cuda_available": cuda_available,
    }
    payload["ok"] = bool(payload["ffmpeg"] and payload["ffprobe"])
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 2


def command_monitor(args: argparse.Namespace) -> int:
    db = Database(_db_path(args))
    station_id = db.upsert_station(args.name, args.url, args.timezone)
    analyzer = _analyzer(args.analyzer, args.ffmpeg)
    monitor = StreamMonitor(
        db=db,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        storage_dir=_storage_path(args),
        analyzer=analyzer,
    )
    session_id = monitor.run(
        station_id=station_id,
        duration_seconds=parse_duration(args.duration),
        segment_seconds=args.segment_seconds,
    )
    print(session_id)
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    db = Database(_db_path(args))
    db.initialize()
    analyzer = _analyzer(args.analyzer, args.ffmpeg)
    if analyzer is None:
        raise ValueError("analyze requires baseline, whisper, or ina analyzer")
    count = analyze_pending_chunks(db, args.session, analyzer)
    write_report_files(db, args.session, Path(_storage_path(args)) / "reports")
    print(json.dumps({"session_id": args.session, "chunks_analyzed": count}))
    return 0


def command_report(args: argparse.Namespace) -> int:
    db = Database(_db_path(args))
    db.initialize()
    if args.write:
        json_path, md_path = write_report_files(
            db,
            args.session,
            Path(_storage_path(args)) / "reports",
        )
        print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2))
    else:
        print(json.dumps(build_report(db, args.session), ensure_ascii=False, indent=2))
    return 0


def command_api(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    app = create_app(_db_path(args))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def command_transcribe(args: argparse.Namespace) -> int:
    from .transcription import SpeechTranscriber, TranscriptionConfig, TranscriptionError

    config = TranscriptionConfig(
        model_size_or_path=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
        beam_size=args.beam_size,
    )
    transcriber = SpeechTranscriber(config)
    try:
        result = transcriber.transcribe(args.audio_file)
    except (FileNotFoundError, ValueError, TranscriptionError) as exc:
        print(f"Transcription error: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"Model: {result.model}")
        print(f"Device: {result.device}")
        print(f"Compute Type: {result.compute_type}")
        print(f"Language: {result.language} (probability: {result.language_probability:.2f})")
        print(f"Duration: {result.duration:.2f}s")
        print(f"Processing Time: {result.processing_time:.2f}s")
        if result.segments:
            print("Segments:")
            for s in result.segments:
                conf_str = f" [conf: {s.confidence:.2f}]" if s.confidence is not None else ""
                print(f"[{s.start:6.2f}s -> {s.end:6.2f}s]{conf_str} {s.text}")
        else:
            print("No speech segments detected.")
        print(f"Transcript: {result.text}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nas-air", description="NAS Air Intelligence")
    parser.add_argument("--db", help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check runtime dependencies")
    doctor.add_argument("--ffmpeg", default=os.getenv("NAS_AIR_FFMPEG", "ffmpeg"))
    doctor.add_argument("--ffprobe", default=os.getenv("NAS_AIR_FFPROBE", "ffprobe"))
    doctor.add_argument("--storage")
    doctor.set_defaults(func=command_doctor)

    monitor = sub.add_parser("monitor", help="monitor a live radio stream")
    monitor.add_argument("--name", required=True)
    monitor.add_argument("--url", required=True)
    monitor.add_argument("--timezone", default="Asia/Baghdad")
    monitor.add_argument("--duration", default="24h")
    monitor.add_argument("--segment-seconds", type=int, default=300)
    monitor.add_argument("--storage")
    monitor.add_argument("--ffmpeg", default=os.getenv("NAS_AIR_FFMPEG", "ffmpeg"))
    monitor.add_argument("--ffprobe", default=os.getenv("NAS_AIR_FFPROBE", "ffprobe"))
    monitor.add_argument(
        "--analyzer",
        choices=["none", "baseline", "whisper", "ina"],
        default="baseline",
    )
    monitor.set_defaults(func=command_monitor)

    analyze = sub.add_parser("analyze", help="analyze indexed chunks")
    analyze.add_argument("--session", required=True)
    analyze.add_argument("--storage")
    analyze.add_argument("--ffmpeg", default=os.getenv("NAS_AIR_FFMPEG", "ffmpeg"))
    analyze.add_argument(
        "--analyzer",
        choices=["baseline", "whisper", "ina"],
        default="baseline",
    )
    analyze.set_defaults(func=command_analyze)

    report = sub.add_parser("report", help="build a session report")
    report.add_argument("--session", required=True)
    report.add_argument("--storage")
    report.add_argument("--write", action="store_true")
    report.set_defaults(func=command_report)

    api = sub.add_parser("api", help="run the read-only API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8787)
    api.set_defaults(func=command_api)

    transcribe = sub.add_parser("transcribe", help="transcribe audio with speech-to-text")
    transcribe.add_argument("audio_file", help="path to audio file (mp3, wav, m4a, etc.)")
    transcribe.add_argument(
        "--model",
        default="tiny",
        help="Whisper model name/path (default: tiny)",
    )
    transcribe.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cuda", "cpu"],
        help="execution device",
    )
    transcribe.add_argument(
        "--compute-type",
        default="auto",
        help="compute type (float16, int8, float32, auto)",
    )
    transcribe.add_argument(
        "--language",
        default="ar",
        help="spoken language code (default: ar)",
    )
    transcribe.add_argument(
        "--beam-size",
        type=int,
        default=5,
        help="beam size (default: 5)",
    )
    transcribe.add_argument(
        "--json",
        action="store_true",
        help="output structured JSON",
    )
    transcribe.set_defaults(func=command_transcribe)

    register_agent(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ToolMissingError, ValueError, KeyError, RuntimeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
