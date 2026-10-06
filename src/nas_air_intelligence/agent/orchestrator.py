"""MonitoringAgent: resolve -> verify -> capture (producer) + analyze (consumer) -> report.

Capture runs in its own thread and never calls the analyzer. SQLite is the hand-off:
the capture thread indexes completed chunks, the analyzer thread consumes chunks with
``analyzed=0``. A slow or crashing analyzer therefore cannot delay recording.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..analysis import Analyzer, FfmpegSilenceAnalyzer, analyze_chunk
from ..db import Database
from ..recorder import StreamMonitor
from .report import write_agent_report
from .resolver import ResolvedStream, StreamResolver
from .states import AgentState
from .store import AgentStore

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 5.0
DRAIN_TIMEOUT_SECONDS = 1800.0
DEGRADED_AFTER_FAILURES = 3
_FAILURE_KINDS = ("analysis_failure", "transcription_incident")

AnalyzerFactory = Callable[[str, str | None, str], Analyzer]


def default_analyzer_factory(name: str, model: str | None, ffmpeg: str) -> Analyzer:
    if name == "baseline":
        return FfmpegSilenceAnalyzer(ffmpeg=ffmpeg)
    from ..analysis import WhisperSpeechAnalyzer
    from ..transcription import SpeechTranscriber, TranscriptionConfig

    config = TranscriptionConfig(model_size_or_path=model) if model else TranscriptionConfig()
    return WhisperSpeechAnalyzer(transcriber=SpeechTranscriber(config), ffmpeg=ffmpeg)


class MonitoringAgent:
    def __init__(
        self,
        db: Database,
        store: AgentStore,
        run_id: str,
        *,
        storage_dir: str | Path = "data",
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        resolver: StreamResolver | None = None,
        analyzer_factory: AnalyzerFactory = default_analyzer_factory,
        monitor_factory: Callable[..., Any] | None = None,
        poll_seconds: float = 5.0,
        drain_timeout: float = DRAIN_TIMEOUT_SECONDS,
    ) -> None:
        self.db = db
        self.store = store
        self.run_id = run_id
        self.storage_dir = Path(storage_dir)
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.resolver = resolver or StreamResolver(ffmpeg=ffmpeg, ffprobe=ffprobe)
        self.analyzer_factory = analyzer_factory
        self.monitor_factory = monitor_factory or self._default_monitor
        self.poll_seconds = poll_seconds
        self.drain_timeout = drain_timeout

        self.stop_event = threading.Event()  # asks the capture thread to finish
        self._capture_done = threading.Event()
        self._session_ready = threading.Event()
        self._halt = threading.Event()  # tells background helper threads to exit
        self._session_id: str | None = None
        self._capture_error: str | None = None
        self._degraded = False

    # ------------------------------------------------------------------ helpers
    def _default_monitor(self, analyzer: Analyzer | None = None) -> StreamMonitor:
        return StreamMonitor(
            db=self.db, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
            storage_dir=self.storage_dir, analyzer=analyzer,
        )  # fmt: skip

    def _state(self, new: AgentState, **fields: Any) -> None:
        self.store.transition(self.run_id, new, **fields)

    def _warn(self, message: str) -> None:
        logger.warning("run %s: %s", self.run_id, message)
        self.store.add_warning(self.run_id, message)

    def _heartbeat_loop(self) -> None:
        while not self._halt.wait(HEARTBEAT_INTERVAL):
            try:
                self.store.heartbeat(self.run_id)
            except Exception:  # heartbeat is best effort; never kill the worker over it
                logger.exception("heartbeat failed")

    # ---------------------------------------------------------------- lifecycle
    def run(self, *, pid: int | None = None, recover: bool = False) -> AgentState:
        self.store.update(self.run_id, pid=pid)
        self.store.heartbeat(self.run_id)
        beat = threading.Thread(target=self._heartbeat_loop, daemon=True, name="agent-heartbeat")
        beat.start()
        try:
            run = self.store.get(self.run_id)
            assert run is not None
            if recover and run.get("session_id"):
                return self._recover(run)
            return self._run_fresh(run)
        except Exception as exc:
            logger.exception("agent run crashed")
            self.store.update(
                self.run_id, state=AgentState.FAILED.value,
                error=f"{exc}\n{traceback.format_exc()[-1500:]}",
            )  # fmt: skip
            self._best_effort_report()
            return AgentState.FAILED
        finally:
            self._halt.set()

    def _run_fresh(self, run: dict[str, Any]) -> AgentState:
        stream = self._resolve_and_verify(run)
        if stream is None:
            return AgentState.STREAM_UNAVAILABLE
        self._state(AgentState.READY)

        station_id = self.db.upsert_station(
            run["station_name"], stream.resolved_stream_url or "", "Asia/Baghdad"
        )
        self._state(AgentState.CAPTURING)
        capture = threading.Thread(
            target=self._capture_thread, args=(station_id, run), daemon=True, name="agent-capture"
        )
        analyzer = threading.Thread(
            target=self._analyzer_thread, args=(run,), daemon=True, name="agent-analyzer"
        )
        capture.start()
        analyzer.start()
        self._supervise(capture)

        if self._session_id is None:
            self._state(AgentState.CAPTURE_FAILED, error=self._capture_error or "no session")
            return AgentState.CAPTURE_FAILED
        return self._finish(analyzer, run)

    def _resolve_and_verify(self, run: dict[str, Any]) -> ResolvedStream | None:
        self._state(AgentState.RESOLVING_STREAM)
        # Resolution and verification happen in one resolver call; the state is advanced
        # in between so status shows what the agent is doing.
        self._state(AgentState.VERIFYING_STREAM)
        stream = self.resolver.resolve(
            run["station_name"], page=run["source_page"], url=run["requested_url"]
        )
        self.store.update(self.run_id, resolved=stream.to_dict())
        if stream.verification_status != "verified":
            self._state(
                AgentState.STREAM_UNAVAILABLE,
                error=stream.details.get("error", "stream verification failed"),
            )
            return None
        return stream

    def _capture_thread(self, station_id: str, run: dict[str, Any]) -> None:
        def on_session(session_id: str) -> None:
            self._session_id = session_id
            self.store.update(self.run_id, session_id=session_id)
            self._session_ready.set()

        try:
            monitor = self.monitor_factory()  # capture only: the monitor gets no analyzer
            monitor.run(
                station_id, run["duration_seconds"], run["segment_seconds"],
                stop_event=self.stop_event, on_session=on_session,
            )  # fmt: skip
        except Exception as exc:
            self._capture_error = f"{type(exc).__name__}: {exc}"
            logger.exception("capture thread failed")
        finally:
            self._capture_done.set()
            self._session_ready.set()

    def _analyzer_thread(self, run: dict[str, Any]) -> None:
        self._session_ready.wait()
        session_id = self._session_id
        if session_id is None:
            return
        try:
            analyzer = self.analyzer_factory(run["analyzer"], run.get("model"), self.ffmpeg)
        except Exception as exc:
            self._warn(f"analyzer '{run['analyzer']}' unavailable ({exc}); using silence baseline")
            self._degraded = True
            analyzer = FfmpegSilenceAnalyzer(ffmpeg=self.ffmpeg)
        consecutive = 0
        drain_started: float | None = None
        while not self._halt.is_set():
            pending = self.db.unanalyzed_chunks(session_id)
            if not pending:
                if self._capture_done.is_set():
                    return
                time.sleep(1.0)
                continue
            if self._capture_done.is_set():
                drain_started = drain_started or time.monotonic()
                if time.monotonic() - drain_started > self.drain_timeout:
                    return
            before = self._failure_count(session_id)
            analyze_chunk(self.db, session_id, pending[0], analyzer)
            if self._failure_count(session_id) > before:
                consecutive += 1
                if consecutive >= DEGRADED_AFTER_FAILURES and not self._degraded:
                    self._degraded = True
                    self._warn(
                        f"processing degraded: {consecutive} consecutive chunks failed analysis; "
                        "capture continues"
                    )
            else:
                consecutive = 0

    def _failure_count(self, session_id: str) -> int:
        return sum(1 for i in self.db.incidents(session_id) if i["kind"] in _FAILURE_KINDS)

    def _supervise(self, capture: threading.Thread) -> None:
        last_chunks, last_progress = 0, time.monotonic()
        stall_warned = False
        run = self.store.get(self.run_id) or {}
        stall_after = 3 * float(run.get("segment_seconds", 300)) + 90
        while capture.is_alive():
            capture.join(self.poll_seconds)
            if self.store.stop_requested(self.run_id) and not self.stop_event.is_set():
                self.stop_event.set()
                self._warn("stopped early on operator request")
            if self._session_id:
                count = len(self.db.chunks(self._session_id))
                if count != last_chunks:
                    last_chunks, last_progress, stall_warned = count, time.monotonic(), False
                elif time.monotonic() - last_progress > stall_after and not stall_warned:
                    stall_warned = True
                    self._warn(f"capture stalled: no new chunk for {stall_after:.0f}s")
        if self._capture_error:
            self._warn(f"capture ended with an error: {self._capture_error}")

    def _finish(self, analyzer: threading.Thread, run: dict[str, Any]) -> AgentState:
        self._state(AgentState.PROCESSING)
        deadline = time.monotonic() + self.drain_timeout + 30
        while analyzer.is_alive() and time.monotonic() < deadline:
            analyzer.join(self.poll_seconds)
        if analyzer.is_alive():
            self._warn("analysis backlog was not fully drained before finalization")
        return self._finalize_and_report(run)

    def _recover(self, run: dict[str, Any]) -> AgentState:
        """Worker died or was lost: close the session, process leftovers, report."""
        session_id = run["session_id"]
        self._session_id = session_id
        session = self.db.session(session_id)
        if session and session["status"] == "running":
            self.db.add_incident(session_id, "worker_lost", "agent worker exited unexpectedly")
            self.db.finish_session(session_id, "stopped")
        self._warn("recovered after the agent worker was lost; capture ended early")
        self._capture_done.set()
        self._session_ready.set()
        if run["state"] in {AgentState.CAPTURING.value}:
            self._state(AgentState.PROCESSING)
        analyzer = threading.Thread(target=self._analyzer_thread, args=(run,), daemon=True)
        analyzer.start()
        while analyzer.is_alive():
            analyzer.join(self.poll_seconds)
        return self._finalize_and_report(run)

    def _finalize_and_report(self, run: dict[str, Any]) -> AgentState:
        session_id = self._session_id
        assert session_id is not None
        current = AgentState(self.store.get(self.run_id)["state"])  # type: ignore[index]
        if current == AgentState.CAPTURING:
            self._state(AgentState.PROCESSING)
        self._state(AgentState.FINALIZING)
        self._state(AgentState.REPORTING)
        report, json_path, md_path, problems = write_agent_report(
            self.db, session_id, self.storage_dir / "reports",
            requested_seconds=float(run["duration_seconds"]), run=self.store.get(self.run_id),
            expects_transcription=(
                run["analyzer"] != "baseline" and not self._degraded_to_baseline()
            ),
        )  # fmt: skip
        for problem in problems:
            self._warn(f"report review: {problem}")
        outcome = report["outcome"]
        if outcome == "COMPLETED" and (self._degraded or self._warnings_present() or problems):
            outcome = "COMPLETED_WITH_WARNINGS"
        for text in report["capture_health"]["warnings"]:
            self._warn(text)
        final = AgentState(outcome)
        self._state(
            final,
            result={
                "outcome": outcome,
                "report_json": str(json_path),
                "report_markdown": str(md_path),
                "gates": {g["gate"]: g["status"] for g in report["capture_health"]["gates"]},
                "executive_summary": report["executive_summary"],
                "review_problems": problems,
            },
        )
        return final

    def _warnings_present(self) -> bool:
        row = self.store.get(self.run_id)
        return bool(row and row["warnings"])

    def _degraded_to_baseline(self) -> bool:
        return self._degraded and any(
            "using silence baseline" in w for w in (self.store.get(self.run_id) or {})["warnings"]
        )

    def _best_effort_report(self) -> None:
        """After an unexpected crash, still preserve a report of whatever was captured."""
        if not self._session_id:
            return
        try:
            run = self.store.get(self.run_id) or {}
            write_agent_report(
                self.db, self._session_id, self.storage_dir / "reports",
                requested_seconds=float(run.get("duration_seconds", 0)), run=run,
            )  # fmt: skip
        except Exception:
            logger.exception("best-effort report failed")
