"""Agent tests. Everything is offline: fake monitor, fake analyzer, temp SQLite files."""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nas_air_intelligence.agent import launcher
from nas_air_intelligence.agent.orchestrator import MonitoringAgent
from nas_air_intelligence.agent.report import REQUIRED_SECTIONS, review_report, write_agent_report
from nas_air_intelligence.agent.resolver import (
    StreamResolver,
    VerificationResult,
    classify_stream,
    extract_candidates,
    parse_playlist,
)
from nas_air_intelligence.agent.states import (
    AgentState,
    InvalidTransition,
    check_transition,
)
from nas_air_intelligence.agent.store import AgentStore
from nas_air_intelligence.agent.timeline import (
    build_timeline,
    format_timeline,
    recurrent_candidates,
)
from nas_air_intelligence.cli import main
from nas_air_intelligence.db import Database
from nas_air_intelligence.util import isoformat

# --------------------------------------------------------------------------- resolver

PAGE = """
<html><head><script src="/static/player.js"></script></head><body>
<img src="https://cdn.example.org/logo.png">
<a href="https://facebook.com/station">fb</a>
<audio controls><source src="https://a4.example.net:6970/radio.mp3" type="audio/mpeg"></audio>
<script>var cfg = {"hls":"https:\\/\\/live.example.net\\/hls\\/index.m3u8"};</script>
</body></html>
"""


def test_extract_candidates_normalizes_and_filters():
    found = extract_candidates(PAGE, "https://station.example/ar/radio")
    urls = [c.url for c in found]
    assert urls[0] == "https://a4.example.net:6970/radio.mp3"  # audio tag ranks first
    assert "https://live.example.net/hls/index.m3u8" in urls  # escaped slashes cleaned
    assert not any("logo.png" in u or "facebook" in u or u.endswith(".js") for u in urls)


def test_extract_candidates_relative_audio_src():
    html = '<audio src="/live/stream.aac"></audio>'
    found = extract_candidates(html, "https://station.example/radio")
    assert found[0].url == "https://station.example/live/stream.aac"


def test_classify_and_playlist():
    assert classify_stream("https://x/a.m3u8", None, {}) == "hls"
    assert classify_stream("https://x/s", "audio/mpeg", {"icy-name": "R"}) == "shoutcast"
    assert classify_stream("https://x/s", "audio/aacp", {"Server": "Icecast 2.4"}) == "icecast"
    assert classify_stream("https://x/a.mp3", "audio/mpeg", {}) == "mp3"
    assert parse_playlist("[playlist]\nFile1=http://h:8000/live\nTitle1=x") == "http://h:8000/live"


def _resolver(pages: dict[str, str], ok_urls: set[str]) -> StreamResolver:
    def fetch(url: str) -> tuple[str, dict[str, str]]:
        if url not in pages:
            raise OSError("404")
        return pages[url], {}

    def verify(url: str) -> VerificationResult:
        if url in ok_urls:
            return VerificationResult(True, "ok", "audio/mpeg", "mp3", 44100, 2, 6.0, -20.0)
        return VerificationResult(False, "unreachable")

    return StreamResolver(fetch=fetch, verifier=verify)


def test_resolver_discovers_and_verifies_from_page():
    page = "https://station.example/radio"
    resolver = _resolver({page: PAGE, "https://station.example/static/player.js": ""},
                         {"https://live.example.net/hls/index.m3u8"})  # fmt: skip
    resolved = resolver.resolve("Station", page=page)
    assert resolved.verification_status == "verified"
    assert resolved.resolved_stream_url == "https://live.example.net/hls/index.m3u8"
    assert resolved.stream_type == "hls"
    assert resolved.codec == "mp3"
    assert resolved.verified_at
    assert len(resolved.details["attempts"]) == 2  # first candidate failed, second passed


def test_resolver_direct_url_and_failures():
    direct = _resolver({}, {"https://s/live.mp3"}).resolve("S", url="https://s/live.mp3")
    assert direct.verification_status == "verified" and direct.source_page is None
    bad = _resolver({}, set()).resolve("S", url="https://s/live.mp3")
    assert bad.verification_status == "failed" and bad.resolved_stream_url is None
    empty = _resolver({"https://p": "<html></html>"}, set()).resolve("S", page="https://p")
    assert empty.verification_status == "failed"
    assert "no stream candidates" in empty.details["error"]
    assert _resolver({}, set()).resolve("S", page="https://missing").verification_status == "failed"
    with pytest.raises(RuntimeError):
        _resolver({}, set()).resolve("S")


# ------------------------------------------------------------------------ state machine


def test_state_transitions():
    check_transition(AgentState.CREATED, AgentState.RESOLVING_STREAM)
    check_transition(AgentState.CAPTURING, AgentState.PROCESSING)
    check_transition(AgentState.REPORTING, AgentState.COMPLETED_WITH_WARNINGS)
    for current, new in [
        (AgentState.CREATED, AgentState.CAPTURING),
        (AgentState.COMPLETED, AgentState.CAPTURING),
        (AgentState.READY, AgentState.COMPLETED),
    ]:
        with pytest.raises(InvalidTransition):
            check_transition(current, new)


def test_store_transition_guard_and_warnings(tmp_path: Path):
    store = AgentStore(Database(tmp_path / "a.db"))
    run_id = store.create(station_name="S", source_page=None, requested_url="u", mode="smoke",
                          duration_seconds=60, segment_seconds=10, analyzer="baseline",
                          model=None)  # fmt: skip
    with pytest.raises(InvalidTransition):
        store.transition(run_id, AgentState.CAPTURING)
    store.transition(run_id, AgentState.RESOLVING_STREAM)
    store.add_warning(run_id, "w")
    store.add_warning(run_id, "w")
    assert store.get(run_id)["warnings"] == ["w"]
    assert store.get(run_id)["worker_alive"] is False  # no heartbeat yet
    store.heartbeat(run_id)
    assert store.get(run_id)["worker_alive"] is True


# ----------------------------------------------------------------------------- timeline


def _chunk(chunk_id: str, start: datetime, seconds: float) -> dict[str, Any]:
    return {
        "id": chunk_id, "started_at": isoformat(start), "duration_seconds": seconds,
        "analyzed": 1,
    }  # fmt: skip


def _event(event_id: str, chunk_id: str, kind: str, a: float, b: float, **extra: Any):
    return {"id": event_id, "chunk_id": chunk_id, "kind": kind, "start_offset": a,
            "end_offset": b, "confidence": extra.get("confidence"), "text": extra.get("text"),
            "fingerprint": extra.get("fingerprint"), "label": None}  # fmt: skip


T0 = datetime(2026, 10, 6, 14, 0, 0, tzinfo=UTC)


def test_timeline_absolute_times_priority_gap_and_fill():
    chunks = [_chunk("c1", T0, 200.0), _chunk("c2", T0 + timedelta(seconds=210), 60.0)]
    events = [
        _event("e1", "c1", "speech", 130.0, 190.0, confidence=0.8, text="x"),
        _event("e2", "c1", "silence", 184.0, 194.0, confidence=1.0),  # overrides speech tail
        _event("e3", "c2", "audio", 0.0, 60.0),
    ]
    segments = build_timeline(chunks, events)
    lines = format_timeline(segments)
    assert lines == [
        "14:00:00–14:02:10 unknown_audio [Unknown]",
        "14:02:10–14:03:04 speech [Detected]",
        "14:03:04–14:03:14 silence [Detected]",
        "14:03:14–14:03:20 unknown_audio [Unknown]",
        "14:03:20–14:03:30 capture_gap [Detected]",
        "14:03:30–14:04:30 unknown_audio [Unknown]",
    ]
    assert segments[0].start == T0


def test_timeline_low_confidence_speech_is_only_likely_and_no_music_invented():
    chunks = [_chunk("c1", T0, 30.0)]
    events = [
        _event("e1", "c1", "speech", 0, 10, confidence=0.1, text="x"),
        _event("e2", "c1", "noise", 10, 20),
    ]
    kinds = {(s.kind, s.tier) for s in build_timeline(chunks, events)}
    assert ("speech", "Likely") in kinds
    assert not any(k == "music" for k, _ in kinds)


def test_timeline_absorbs_boundary_slivers():
    chunks = [_chunk("c1", T0, 30.0), _chunk("c2", T0 + timedelta(seconds=30), 0.2)]
    segments = build_timeline(chunks, [_event("e1", "c1", "speech", 0, 30, confidence=0.9)])
    assert [(s.kind, s.duration_seconds) for s in segments] == [("speech", 30.2)]


def test_recurrent_candidates_exact_fingerprint_only():
    chunks = [_chunk(f"c{i}", T0 + timedelta(seconds=60 * i), 60) for i in range(3)]
    events = [
        _event("e0", "c0", "speech", 0, 5, fingerprint="AAAA"),
        _event("e1", "c1", "speech", 0, 5, fingerprint="AAAA"),
        _event("e2", "c2", "speech", 0, 5, fingerprint="BBBB"),
    ]
    found = recurrent_candidates(chunks, events)
    assert len(found) == 1 and found[0]["occurrences"] == 2
    assert found[0]["label"] == "recurrent_audio_candidate"


# ---------------------------------------------------------------------------- lifecycle


class FakeMonitor:
    """Stands in for StreamMonitor: writes chunk rows, honours stop_event."""

    def __init__(self, db: Database, *, chunks: int = 2, fail_after: bool = False,
                 fail_before_session: bool = False, wait_for_stop: bool = False):  # fmt: skip
        self.db, self.chunks = db, chunks
        self.fail_after, self.fail_before = fail_after, fail_before_session
        self.wait_for_stop = wait_for_stop

    def run(self, station_id, duration, segment, *, stop_event, on_session):
        if self.fail_before:
            raise RuntimeError("ffmpeg missing")
        session_id = self.db.create_session(station_id, duration, int(segment), "x")
        on_session(session_id)
        now = datetime.now(UTC)
        for i in range(self.chunks):
            start = now - timedelta(seconds=60 * (self.chunks - i))
            self.db.add_chunk(
                session_id,
                f"chunk-{i}.mp3",
                isoformat(start),
                isoformat(start + timedelta(seconds=60)),
                60.0,
                f"sha{i}",
            )
        if self.wait_for_stop:
            assert stop_event.wait(10)
            self.db.add_incident(session_id, "manual_stop", "stop requested")
            self.db.finish_session(session_id, "stopped")
            return session_id
        if self.fail_after:
            self.db.add_incident(session_id, "monitor_error", "boom")
            self.db.finish_session(session_id, "failed", "boom")
            raise RuntimeError("boom")
        self.db.finish_session(session_id, "completed")
        return session_id


class GoodAnalyzer:
    def analyze(self, path, duration):
        return [
            {"kind": "speech", "start_offset": 0.0, "end_offset": 40.0, "confidence": 0.7,
             "label": "faster-whisper", "text": "short sample", "fingerprint": None,
             "metadata": {"language": "ar"}},
            {"kind": "silence", "start_offset": 40.0, "end_offset": 45.0, "confidence": 1.0,
             "label": "ffmpeg-silencedetect"},
        ]  # fmt: skip


class CrashingAnalyzer:
    def analyze(self, path, duration):
        raise RuntimeError("CUDA out of memory")


def _setup(tmp_path: Path, **run_kwargs: Any):
    db = Database(tmp_path / "t.db")
    store = AgentStore(db)
    run_id = store.create(
        station_name="Test FM", source_page=None, requested_url="https://s/live.mp3",
        mode="smoke", duration_seconds=120, segment_seconds=60,
        analyzer=run_kwargs.pop("analyzer", "whisper"), model=None,
    )  # fmt: skip
    return db, store, run_id


def _agent(db, store, run_id, tmp_path, monitor, analyzer=GoodAnalyzer, verified=True, **kw):
    def verify(url):
        return VerificationResult(verified, "ok" if verified else "unreachable", "audio/mpeg",
                                  "mp3", 44100, 2, 6.0, -20.0)  # fmt: skip

    def factory(name, model, ffmpeg):
        if isinstance(analyzer, Exception):
            raise analyzer
        return analyzer()

    return MonitoringAgent(
        db, store, run_id, storage_dir=tmp_path / "data",
        resolver=StreamResolver(fetch=lambda u: ("", {}), verifier=verify),
        analyzer_factory=factory, monitor_factory=lambda: monitor, poll_seconds=0.05, **kw,
    )  # fmt: skip


def test_full_lifecycle_completes_and_writes_report(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    agent = _agent(db, store, run_id, tmp_path, FakeMonitor(db))
    assert agent.run(pid=123) == AgentState.COMPLETED
    run = store.get(run_id)
    assert run["state"] == "COMPLETED" and run["pid"] == 123
    assert run["resolved"]["verification_status"] == "verified"
    result = run["result"]
    assert set(result["gates"].values()) == {"pass"}
    report = json.loads(Path(result["report_json"]).read_text(encoding="utf-8"))
    assert all(
        report[s] not in (None, "", {})
        for s in REQUIRED_SECTIONS
        if s != "recurrent_audio_candidates"
    )  # noqa: E501
    assert report["review"]["ok"]
    assert "Executive Summary" in Path(result["report_markdown"]).read_text(encoding="utf-8")
    assert all(c["analyzed"] for c in db.chunks(run["session_id"]))


def test_stream_unavailable_stops_before_capture(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    agent = _agent(db, store, run_id, tmp_path, FakeMonitor(db), verified=False)
    assert agent.run() == AgentState.STREAM_UNAVAILABLE
    assert store.get(run_id)["session_id"] is None
    assert db.sessions() == []


def test_capture_failure_without_data(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    agent = _agent(db, store, run_id, tmp_path, FakeMonitor(db, fail_before_session=True))
    assert agent.run() == AgentState.CAPTURE_FAILED
    assert "ffmpeg missing" in store.get(run_id)["error"]


def test_capture_failure_keeps_captured_data_and_degrades(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    agent = _agent(db, store, run_id, tmp_path, FakeMonitor(db, fail_after=True))
    assert agent.run() == AgentState.COMPLETED_WITH_WARNINGS
    run = store.get(run_id)
    assert len(db.chunks(run["session_id"])) == 2
    assert any("capture ended with an error" in w for w in run["warnings"])
    assert Path(run["result"]["report_json"]).exists()


def test_analysis_failure_is_degraded_not_failed(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    agent = _agent(db, store, run_id, tmp_path, FakeMonitor(db, chunks=4), CrashingAnalyzer)
    assert agent.run() == AgentState.COMPLETED_WITH_WARNINGS
    run = store.get(run_id)
    assert any("processing degraded" in w for w in run["warnings"])
    assert run["result"]["gates"]["transcription"] == "fail"
    assert len(db.chunks(run["session_id"])) == 4  # capture data preserved
    kinds = {i["kind"] for i in db.incidents(run["session_id"])}
    assert "analysis_failure" in kinds


def test_analyzer_unavailable_falls_back_with_warning(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    agent = _agent(db, store, run_id, tmp_path, FakeMonitor(db), RuntimeError("no CUDA"))
    assert agent.run() == AgentState.COMPLETED_WITH_WARNINGS
    assert any("unavailable" in w for w in store.get(run_id)["warnings"])


def test_stop_request_finalizes_gracefully(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    agent = _agent(db, store, run_id, tmp_path, FakeMonitor(db, wait_for_stop=True))
    worker = threading.Thread(target=agent.run)
    worker.start()
    deadline = time.monotonic() + 10
    while store.get(run_id)["state"] != "CAPTURING" and time.monotonic() < deadline:
        time.sleep(0.02)
    store.request_stop(run_id)
    worker.join(20)
    assert not worker.is_alive()
    run = store.get(run_id)
    assert run["state"] == "COMPLETED_WITH_WARNINGS"
    assert any("stopped early" in w for w in run["warnings"])
    assert db.session(run["session_id"])["status"] == "stopped"


def test_recovery_after_worker_loss(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    station = db.upsert_station("Test FM", "https://s/live.mp3")
    FakeMonitor(db).run(station, 120, 60, stop_event=threading.Event(),
                        on_session=lambda sid: store.update(run_id, session_id=sid))  # fmt: skip
    session_id = store.get(run_id)["session_id"]
    with db.connect() as conn:  # simulate the crash: session left running, state mid-capture
        conn.execute("UPDATE sessions SET status='running', ended_at=NULL WHERE id=?",
                     (session_id,))  # fmt: skip
        conn.execute("UPDATE chunks SET analyzed=0 WHERE session_id=?", (session_id,))
    store.update(run_id, state="CAPTURING", heartbeat_at="2000-01-01T00:00:00Z")
    info = launcher.describe(db, store, store.get(run_id))
    assert info["worker_alive"] is False and "attention" in info

    agent = _agent(db, store, run_id, tmp_path, FakeMonitor(db))
    assert agent.run(recover=True) == AgentState.COMPLETED_WITH_WARNINGS
    run = store.get(run_id)
    assert db.session(session_id)["status"] == "stopped"
    assert all(c["analyzed"] for c in db.chunks(session_id))
    assert any(i["kind"] == "worker_lost" for i in db.incidents(session_id))
    assert Path(run["result"]["report_json"]).exists()


def _sleeper():
    import subprocess
    import sys

    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_reap_orphan_ffmpeg_kills_process_and_clears_pid_file(tmp_path: Path, monkeypatch):
    from nas_air_intelligence import recorder

    monkeypatch.setattr(recorder, "_is_ffmpeg_process", lambda pid: True)
    proc = _sleeper()
    try:
        (tmp_path / recorder.PID_FILE).write_text(str(proc.pid), encoding="utf-8")
        assert recorder.reap_orphan_ffmpeg(tmp_path) == proc.pid
        assert proc.wait(timeout=15) is not None
        assert not (tmp_path / recorder.PID_FILE).exists()
    finally:
        proc.kill()


def test_reap_orphan_ffmpeg_ignores_non_ffmpeg_pid_and_missing_file(tmp_path: Path, monkeypatch):
    from nas_air_intelligence import recorder

    assert recorder.reap_orphan_ffmpeg(tmp_path) is None  # no pid file
    monkeypatch.setattr(recorder, "_is_ffmpeg_process", lambda pid: False)
    proc = _sleeper()
    try:
        (tmp_path / recorder.PID_FILE).write_text(str(proc.pid), encoding="utf-8")
        assert recorder.reap_orphan_ffmpeg(tmp_path) is None  # PID reused by another program
        assert proc.poll() is None  # untouched
    finally:
        proc.kill()


# ------------------------------------------------------------------------------ report


def test_review_report_flags_missing_sections_and_long_excerpts(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    _agent(db, store, run_id, tmp_path, FakeMonitor(db)).run()
    run = store.get(run_id)
    report, _, _, problems = write_agent_report(
        db, run["session_id"], tmp_path / "r", requested_seconds=120, run=run
    )
    assert problems == []
    broken = {**report, "timeline": {}, "executive_summary": ""}
    found = review_report(broken, "x" * 700)
    assert any("timeline" in p for p in found) and any("executive_summary" in p for p in found)
    report["speech_intelligence"]["excerpts"] = [{"confidence": 1, "text": "w" * 10000}]
    assert any("excerpt policy" in p for p in review_report(report, "x" * 700))


# ----------------------------------------------------------------- background / CLI


def test_resolve_duration_modes():
    assert launcher.resolve_duration("smoke", None) == ("smoke", 600.0)
    assert launcher.resolve_duration("validation", None) == ("validation", 7200.0)
    assert launcher.resolve_duration(None, "45m") == ("custom", 2700.0)
    assert launcher.resolve_duration("smoke", "15m") == ("smoke", 900.0)
    with pytest.raises(ValueError):
        launcher.resolve_duration(None, None)
    with pytest.raises(ValueError):
        launcher.resolve_duration(None, "24h")


def test_spawn_worker_is_detached_and_logs(tmp_path: Path, monkeypatch):
    seen: dict[str, Any] = {}

    class FakeProc:
        pid = 4242

    def fake_popen(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return FakeProc()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    pid = launcher.spawn_worker("run-1234567890", db_path="d.db", storage="data",
                                log_dir=tmp_path / "logs", recover=True)  # fmt: skip
    assert pid == 4242
    assert seen["command"][:3] == [sys.executable, "-m", "nas_air_intelligence.cli"]
    assert "--recover" in seen["command"] and "_run" in seen["command"]
    if sys.platform == "win32":
        flags = seen["kwargs"]["creationflags"]
        assert flags & 0x00000008 and flags & 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        assert seen["kwargs"]["start_new_session"] is True
    assert len(list((tmp_path / "logs").glob("agent-run-1234*"))) == 2


def test_cli_start_status_stop_result(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr("nas_air_intelligence.agent.cli.spawn_worker", lambda *a, **k: 777)
    db_path = str(tmp_path / "cli.db")
    base = ["--db", db_path]
    assert main([*base, "agent", "start", "--station", "S", "--url", "https://s/x.mp3",
                 "--mode", "smoke", "--storage", str(tmp_path / "d")]) == 0  # fmt: skip
    started = json.loads(capsys.readouterr().out)
    assert started["worker_pid"] == 777 and started["duration_seconds"] == 600.0

    assert main([*base, "agent", "status", started["run_id"][:8]]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "CREATED" and status["worker_alive"] is False

    assert main([*base, "agent", "result", started["run_id"]]) == 1  # not finished
    capsys.readouterr()
    assert main([*base, "agent", "stop", started["run_id"], "--storage", str(tmp_path / "d")]) == 0
    assert "worker lost before capture" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main([*base, "agent", "start", "--station", "S", "--mode", "smoke"])  # no page/url


def test_result_for_plain_monitor_session(tmp_path: Path, capsys):
    db_path = str(tmp_path / "plain.db")
    db = Database(db_path)
    station = db.upsert_station("Plain FM", "https://s/x")
    FakeMonitor(db).run(station, 120, 60, stop_event=threading.Event(), on_session=lambda s: None)
    session_id = db.sessions()[0]["id"]
    assert main(["--db", db_path, "agent", "result", session_id]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["session_id"] == session_id and payload["timeline_head"]


# ------------------------------------------------- coverage, completion, language, recurrence


def test_timeline_covers_captured_audio_even_with_sparse_events():
    """The 2h validation had ~27% of airtime with no explicit event; it must show as unknown."""
    chunks = [_chunk(f"c{i}", T0 + timedelta(seconds=60 * i), 60.0) for i in range(5)]
    events = [
        _event(f"e{i}", f"c{i}", "speech", 0.0, 44.0, confidence=0.8, text="t") for i in range(5)
    ]
    segments = build_timeline(chunks, events)
    covered = sum(s.duration_seconds for s in segments if s.kind != "capture_gap")
    assert covered == pytest.approx(300.0, abs=0.01)
    unknown = sum(s.duration_seconds for s in segments if s.kind == "unknown_audio")
    assert unknown == pytest.approx(5 * 16.0, abs=0.01)
    assert not any(s.kind == "music" for s in segments)


def test_report_metrics_and_language_and_recurrence_status(tmp_path: Path):
    db, store, run_id = _setup(tmp_path)
    _agent(db, store, run_id, tmp_path, FakeMonitor(db)).run()
    run = store.get(run_id)
    report, _, md_path, _ = write_agent_report(
        db, run["session_id"], tmp_path / "r", requested_seconds=120, run=run
    )
    metrics = report["summary_metrics"]
    assert metrics["timeline_coverage_of_captured"] == pytest.approx(1.0, abs=0.01)
    assert metrics["speech_seconds"] + metrics["silence_seconds"] + metrics["unknown_seconds"] == (
        pytest.approx(metrics["captured_seconds"], abs=0.5)
    )
    rec = report["recurrent_audio_candidates"]
    assert rec["recurrent_audio_detection"] == "NOT VERIFIED"
    assert rec["acoustic_fingerprint_primitive"] == "READY"
    text = md_path.read_text(encoding="utf-8")
    assert "Key Metrics" in text and "NOT VERIFIED" in text


def _speech_event(meta: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "speech", "text": "x", "confidence": 0.8, "start_offset": 0,
            "end_offset": 1, "metadata": meta}  # fmt: skip


def test_language_reporting_forced_auto_and_legacy():
    from nas_air_intelligence.agent.report import _language_reporting

    forced = _language_reporting(
        [
            _speech_event(
                {"language": "ar", "configured_language": "ar", "language_probability": 1.0}
            )
        ]
    )
    assert forced["configured_language"] == "ar"
    assert "language_probability" not in json.dumps(forced)
    assert "forced" in forced["language_detection"]

    auto = _language_reporting(
        [
            _speech_event(
                {"language": "ar", "configured_language": None, "language_probability": 0.9}
            )
        ]
    )
    assert auto["detected_language"] == "ar" and auto["language_probability_mean"] == 0.9

    legacy = _language_reporting([_speech_event({"language": "ar", "language_probability": 1.0})])
    assert legacy["configured_language"] is None
    assert "language_probability_mean" not in legacy and "predate" in legacy["language_detection"]


def _run_recorder_with_clean_exits(tmp_path: Path, monkeypatch, duration: float):
    from nas_air_intelligence import recorder

    class FakeProc:
        returncode = 0
        pid = 4242

        def __init__(self, *a, **k):
            pass

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(recorder, "find_binary", lambda name: name)
    monkeypatch.setattr(recorder.subprocess, "Popen", FakeProc)
    db = Database(tmp_path / "rec.db")
    station = db.upsert_station("S", "https://s/x")
    monitor = recorder.StreamMonitor(db, storage_dir=tmp_path / "d", reconnect_delay=0.01)
    session_id = monitor.run(station, duration, 10)
    return db, session_id


def test_normal_ffmpeg_completion_is_not_an_incident(tmp_path: Path, monkeypatch):
    db, session_id = _run_recorder_with_clean_exits(tmp_path, monkeypatch, duration=3.0)
    assert db.session(session_id)["status"] == "completed"
    assert [i for i in db.incidents(session_id) if i["kind"] == "ffmpeg_exit"] == []


def test_early_clean_exit_is_still_an_incident(tmp_path: Path, monkeypatch):
    # 25s requested but ffmpeg "exits cleanly" at once: the stream ended early -> incident.
    from nas_air_intelligence import recorder

    monkeypatch.setattr(recorder, "GRACEFUL_END_TOLERANCE_SECONDS", 10.0)
    calls = {"n": 0}

    class FakeProc:
        returncode = 0
        pid = 4242

        def __init__(self, *a, **k):
            calls["n"] += 1

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(recorder, "find_binary", lambda name: name)
    monkeypatch.setattr(recorder.subprocess, "Popen", FakeProc)
    db = Database(tmp_path / "rec2.db")
    station = db.upsert_station("S", "https://s/x")
    monitor = recorder.StreamMonitor(db, storage_dir=tmp_path / "d", reconnect_delay=0.01)
    session_id = monitor.run(station, 12.0, 10)
    assert any(i["kind"] == "ffmpeg_exit" for i in db.incidents(session_id))
    assert calls["n"] >= 2  # reconnected


def test_resolver_finds_stream_hidden_in_late_js_chunk():
    """Regression from the real al-nakhla.net page (Next.js): the URL is in new Audio(...)."""
    page = "https://station.example/ar/radio"
    scripts = [f"/_next/static/chunks/{i}-abc.js" for i in range(8)]
    html = "".join(f'<script src="{s}"></script>' for s in scripts)
    html += '<a href="https://alt.invalid/ar/radio">alt</a>'
    pages = {page: html, **{f"https://station.example{s}": "var x=1;" for s in scripts}}
    pages["https://station.example" + scripts[7]] = (
        'useEffect(()=>{let e=new Audio("https://a4.example.net:6970/radio.mp3");})'
    )
    resolver = _resolver(pages, {"https://a4.example.net:6970/radio.mp3"})
    resolved = resolver.resolve("Station", page=page)
    assert resolved.verification_status == "verified"
    assert resolved.resolved_stream_url == "https://a4.example.net:6970/radio.mp3"
    assert resolved.details["attempts"][0]["origin"] == "js-audio"  # tried before page links


def test_stop_ffmpeg_asks_politely_then_kills_as_fallback():
    import subprocess as sp

    from nas_air_intelligence.recorder import _stop_ffmpeg

    class Stdin:
        data = ""

        def write(self, text):
            self.data += text

        def flush(self):
            pass

    class Polite:
        stdin = Stdin()
        terminated = False

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            self.terminated = True

    polite = Polite()
    _stop_ffmpeg(polite)
    assert polite.stdin.data == "q\n" and not polite.terminated

    class Stubborn(Polite):
        stdin = Stdin()
        calls = 0
        killed = False

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls <= 2:
                raise sp.TimeoutExpired("ffmpeg", timeout)
            return 0

        def kill(self):
            self.killed = True

    stubborn = Stubborn()
    _stop_ffmpeg(stubborn)
    assert stubborn.terminated and stubborn.killed


def test_excerpts_skip_duplicates_and_repetitive_hallucinations():
    from nas_air_intelligence.agent.report import _speech_intelligence

    def ev(text, conf):
        return {"kind": "speech", "text": text, "confidence": conf, "start_offset": 0,
                "end_offset": 1, "metadata": {}}  # fmt: skip

    events = [
        ev("la la la la la", 0.99),
        ev("good morning listeners", 0.8),
        ev("good morning listeners", 0.8),
        ev("traffic is heavy downtown", 0.7),
    ]
    texts = [e["text"] for e in _speech_intelligence(events)["excerpts"]]
    assert texts == ["good morning listeners", "traffic is heavy downtown"]
