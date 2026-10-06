from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .analysis import Analyzer, analyze_pending_chunks
from .db import Database
from .ffmpeg import find_binary, probe_duration
from .reporting import write_report_files
from .util import isoformat, sha256_file

# A clean ffmpeg exit this close to the deadline is the requested duration ending.
GRACEFUL_END_TOLERANCE_SECONDS = 10.0


class MonitorError(RuntimeError):
    pass


def _completed_audio_files(directory: Path) -> list[Path]:
    return sorted(
        path for path in directory.glob("*.mp3") if path.is_file() and path.stat().st_size > 0
    )


def _stop_ffmpeg(process: subprocess.Popen) -> None:
    """Ask ffmpeg to quit so it finalizes the in-flight chunk; hard-kill only as a fallback.

    On Windows terminate() is TerminateProcess, which drops the partially written segment.
    """
    try:
        if process.stdin:
            process.stdin.write("q\n")
            process.stdin.flush()
        process.wait(timeout=10)
        return
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


PID_FILE = "ffmpeg.pid"


def _is_ffmpeg_process(pid: int) -> bool:
    """True only if ``pid`` is alive and is an ffmpeg process (guards against PID reuse)."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout  # fmt: skip
        else:
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return False
    return "ffmpeg" in out.lower()


def reap_orphan_ffmpeg(output_dir: Path) -> int | None:
    """Kill an ffmpeg left running after its worker died; returns the PID it killed, if any.

    Child processes are not terminated with their parent on Windows, so a lost worker leaves
    ffmpeg recording into the session directory with nobody indexing the chunks.
    """
    pid_file = output_dir / PID_FILE
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    killed: int | None = None
    if _is_ffmpeg_process(pid):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=15, check=False,
            )  # fmt: skip
        else:
            import signal

            os.kill(pid, signal.SIGTERM)
        killed = pid
    pid_file.unlink(missing_ok=True)
    return killed


def _is_stable(path: Path, minimum_age_seconds: float = 1.5) -> bool:
    return (time.time() - path.stat().st_mtime) >= minimum_age_seconds


class StreamMonitor:
    def __init__(
        self,
        db: Database,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        storage_dir: str | Path = "data",
        analyzer: Analyzer | None = None,
        reconnect_delay: float = 5.0,
    ) -> None:
        self.db = db
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.storage_dir = Path(storage_dir)
        self.analyzer = analyzer
        self.reconnect_delay = reconnect_delay

    def _index_new_chunks(
        self,
        session_id: str,
        output_dir: Path,
        seen: set[str],
        *,
        include_newest: bool = True,
    ) -> int:
        indexed = 0
        files = _completed_audio_files(output_dir)
        if not include_newest and files:
            files = files[:-1]
        for path in files:
            key = str(path.resolve())
            if key in seen or not _is_stable(path):
                continue
            try:
                duration = probe_duration(path, self.ffprobe)
            except (subprocess.CalledProcessError, ValueError, KeyError):
                continue
            ended_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            started_at = ended_at.timestamp() - duration
            start_dt = datetime.fromtimestamp(started_at, tz=UTC)
            chunk_id = self.db.add_chunk(
                session_id=session_id,
                path=key,
                started_at=isoformat(start_dt),
                ended_at=isoformat(ended_at),
                duration_seconds=duration,
                sha256=sha256_file(path),
            )
            seen.add(key)
            if chunk_id:
                indexed += 1
        return indexed

    def run(
        self,
        station_id: str,
        duration_seconds: float,
        segment_seconds: int = 300,
        *,
        stop_event: threading.Event | None = None,
        on_session: Callable[[str], None] | None = None,
    ) -> str:
        if segment_seconds < 10:
            raise ValueError("segment_seconds must be at least 10")
        station = self.db.station(station_id)
        if not station:
            raise KeyError(f"unknown station: {station_id}")

        ffmpeg_executable = find_binary(self.ffmpeg)
        find_binary(self.ffprobe)

        session_root = self.storage_dir / "recordings"
        session_root.mkdir(parents=True, exist_ok=True)
        session_id = self.db.create_session(
            station_id,
            duration_seconds,
            segment_seconds,
            "pending",
        )
        output_dir = session_root / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if on_session:
            on_session(session_id)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE sessions SET output_dir=? WHERE id=?",
                (str(output_dir.resolve()), session_id),
            )

        deadline = time.monotonic() + duration_seconds
        seen: set[str] = set()
        next_segment_number = 0

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                output_pattern = str(output_dir / "chunk-%06d.mp3")
                command = [
                    ffmpeg_executable,
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_delay_max",
                    "10",
                    "-i",
                    station["stream_url"],
                    "-t",
                    str(max(1.0, remaining)),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "64k",
                    "-f",
                    "segment",
                    "-segment_time",
                    str(segment_seconds),
                    "-segment_start_number",
                    str(next_segment_number),
                    "-reset_timestamps",
                    "1",
                    output_pattern,
                ]
                attempt_log = output_dir / f"ffmpeg-{next_segment_number:06d}.log"
                with attempt_log.open("w", encoding="utf-8") as log_handle:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,  # lets us ask ffmpeg to finalize with 'q'
                        stdout=subprocess.DEVNULL,
                        stderr=log_handle,
                        text=True,
                        env=os.environ.copy(),
                    )
                    (output_dir / PID_FILE).write_text(str(process.pid), encoding="utf-8")
                    while process.poll() is None:
                        self._index_new_chunks(session_id, output_dir, seen, include_newest=False)
                        if self.analyzer:
                            analyze_pending_chunks(self.db, session_id, self.analyzer)
                        if time.monotonic() >= deadline or (stop_event and stop_event.is_set()):
                            _stop_ffmpeg(process)
                            break
                        time.sleep(2.0)

                (output_dir / PID_FILE).unlink(missing_ok=True)
                self._index_new_chunks(session_id, output_dir, seen, include_newest=True)
                if self.analyzer:
                    analyze_pending_chunks(self.db, session_id, self.analyzer)

                next_segment_number = len(_completed_audio_files(output_dir))
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (stop_event and stop_event.is_set()):
                    break
                if process.returncode == 0 and remaining <= GRACEFUL_END_TOLERANCE_SECONDS:
                    break  # ffmpeg reached its requested -t: graceful completion, not an incident
                stderr_lines = attempt_log.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()[-30:]
                details = "\n".join(stderr_lines)[-4000:] if stderr_lines else None
                self.db.add_incident(
                    session_id,
                    "ffmpeg_exit",
                    f"returncode={process.returncode}\n{details or ''}".strip(),
                )
                time.sleep(min(self.reconnect_delay, max(0.0, remaining)))

            # The final file may have been modified less than the stability threshold.
            time.sleep(1.6)
            self._index_new_chunks(session_id, output_dir, seen)
            if self.analyzer:
                analyze_pending_chunks(self.db, session_id, self.analyzer)
            if stop_event and stop_event.is_set():
                self.db.add_incident(session_id, "manual_stop", "stop requested")
                self.db.finish_session(session_id, "stopped")
            else:
                self.db.finish_session(session_id, "completed")
        except KeyboardInterrupt:
            self.db.add_incident(session_id, "manual_stop", "monitor interrupted by operator")
            self.db.finish_session(session_id, "stopped")
        except Exception as exc:
            self.db.add_incident(session_id, "monitor_error", str(exc))
            self.db.finish_session(session_id, "failed", str(exc))
            raise
        finally:
            reports_dir = self.storage_dir / "reports"
            write_report_files(self.db, session_id, reports_dir)
        return session_id
