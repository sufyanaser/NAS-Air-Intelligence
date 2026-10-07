"""Desktop sidecar: loopback health endpoint, token auth, shutdown, parent-death exit."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nas_air_intelligence.agent.states import AgentState
from nas_air_intelligence.sidecar import SidecarServer, health_payload

TOKEN = "test-token"


def _get(port: int, path: str, token: str | None = TOKEN, method: str = "GET", body: bytes = b""):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=body or None)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    if body:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=5)


@pytest.fixture
def server(tmp_path: Path):
    srv = SidecarServer(TOKEN, storage=tmp_path / "data")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def no_real_worker(monkeypatch):
    """Agent endpoints must never spawn a real ffmpeg/network subprocess under pytest."""
    calls: list[dict] = []

    def fake_spawn_worker(run_id, **kwargs):
        calls.append({"run_id": run_id, **kwargs})
        return 4242

    monkeypatch.setattr("nas_air_intelligence.agent.launcher.spawn_worker", fake_spawn_worker)
    return calls


def test_health_ready_with_token(server):
    with _get(server.server_address[1], "/health") as resp:
        body = json.loads(resp.read())
    assert resp.status == 200
    assert body["status"] == "ready" and body["service"] == "nas-air-sidecar"
    assert set(body) >= {"version", "python", "pid", "ffmpeg", "ffprobe"}


@pytest.mark.parametrize("token", [None, "wrong"])
def test_rejects_missing_or_wrong_token(server, token):
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(server.server_address[1], "/health", token)
    assert err.value.code == 401


def test_unknown_path_is_404(server):
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(server.server_address[1], "/nope")
    assert err.value.code == 404


def test_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_requires_token():
    with pytest.raises(ValueError):
        SidecarServer("")


def test_health_payload_shape():
    assert health_payload()["status"] == "ready"


def _spawn(env_extra: dict[str, str] | None = None):
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "NAS_AIR_SIDECAR_TOKEN": TOKEN, "PYTHONPATH": src, **(env_extra or {})}
    return subprocess.Popen(
        [sys.executable, "-m", "nas_air_intelligence.sidecar"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )  # fmt: skip


def test_process_ready_line_shutdown_endpoint():
    proc = _spawn()
    try:
        ready = json.loads(proc.stdout.readline())
        assert ready["event"] == "ready" and ready["port"] > 0
        with _get(ready["port"], "/health") as resp:
            assert json.loads(resp.read())["pid"] == ready["pid"]
        _get(ready["port"], "/shutdown", method="POST").close()
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()


def test_process_exits_when_parent_closes_stdin():
    proc = _spawn()
    try:
        json.loads(proc.stdout.readline())
        proc.stdin.close()  # what the OS does to our stdin when the desktop app dies
        assert proc.wait(timeout=10) == 0
    finally:
        proc.kill()


def test_process_refuses_to_start_without_token():
    env = {k: v for k, v in os.environ.items() if k != "NAS_AIR_SIDECAR_TOKEN"}
    src = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "nas_air_intelligence.sidecar"],
        env={**env, "PYTHONPATH": src}, capture_output=True, timeout=30,
    )  # fmt: skip
    assert proc.returncode == 2


def test_default_storage_dir_is_per_user_not_cwd(monkeypatch, tmp_path: Path):
    from nas_air_intelligence import sidecar

    monkeypatch.delenv("NAS_AIR_STORAGE", raising=False)
    monkeypatch.setattr(sidecar.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    storage = sidecar.default_storage_dir()
    assert storage == tmp_path / "NAS Air Intelligence Data"
    assert storage != tmp_path / "NAS Air Intelligence"  # that is the NSIS install directory

    monkeypatch.setenv("NAS_AIR_STORAGE", str(tmp_path / "override"))
    assert sidecar.default_storage_dir() == tmp_path / "override"


# ------------------------------------------------------------------------------ agent bridge
def _start(server, no_real_worker, **overrides):
    payload = {"station": "Test FM", "url": "https://s/live.mp3", "duration": "10m",
               "analyzer": "baseline", **overrides}  # fmt: skip
    with _get(server.server_address[1], "/agent/start", method="POST",
              body=json.dumps(payload).encode()) as resp:  # fmt: skip
        return json.loads(resp.read())


def test_agent_start_launches_without_a_real_subprocess(server, no_real_worker):
    result = _start(server, no_real_worker)
    assert result["worker_pid"] == 4242 and result["station"] == "Test FM"
    assert no_real_worker and no_real_worker[0]["run_id"] == result["run_id"]
    # storage/db paths handed to the worker are the sidecar's own, not relative to any cwd
    assert no_real_worker[0]["storage"] == server.storage
    assert no_real_worker[0]["db_path"] == server.db_path


def test_agent_start_rejects_missing_fields(server, no_real_worker):
    port = server.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(port, "/agent/start", method="POST", body=json.dumps({"station": "S"}).encode())
    assert err.value.code == 400
    assert not no_real_worker


def test_agent_status_stop_result_journal_timeline_round_trip(server, no_real_worker):
    port = server.server_address[1]
    run_id = _start(server, no_real_worker)["run_id"]

    with _get(port, f"/agent/{run_id}/status") as resp:
        status = json.loads(resp.read())
    assert status["run_id"] == run_id and status["state"] == "CREATED"

    # a short id prefix resolves the same run, matching the CLI's own lookup rules
    with _get(port, f"/agent/{run_id[:8]}/status") as resp:
        assert json.loads(resp.read())["run_id"] == run_id

    with pytest.raises(urllib.error.HTTPError) as err:
        _get(port, f"/agent/{run_id}/result")
    assert err.value.code == 409  # not finished yet

    with _get(port, f"/agent/{run_id}/stop", method="POST") as resp:
        stop = json.loads(resp.read())
    assert stop["run_id"] == run_id

    with _get(port, f"/agent/{run_id}/timeline") as resp:
        timeline = json.loads(resp.read())
    assert timeline == {
        "run_id": run_id, "session_id": None,
        "segments": [], "rendered": [], "current_material": None,
    }  # fmt: skip

    with _get(port, f"/agent/{run_id}/journal") as resp:
        journal = json.loads(resp.read())
    assert journal["run_id"] == run_id and journal["events"] == []

    with pytest.raises(urllib.error.HTTPError) as err:
        _get(port, "/agent/00000000-0000-0000-0000-000000000000/status")
    assert err.value.code == 404


def test_agent_programming_and_export_require_a_started_session(server, no_real_worker):
    port = server.server_address[1]
    run_id = _start(server, no_real_worker)["run_id"]
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(port, f"/agent/{run_id}/programming")
    assert err.value.code == 409
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(port, f"/agent/{run_id}/export/xlsx", method="POST")
    assert err.value.code == 409


def _attach_real_session(server, run_id: str) -> str:
    """Gives a started run a real session with chunks+events, as capture would."""
    station = server.db.upsert_station("Test FM", "https://s/live.mp3")
    session_id = server.db.create_session(station, 3600.0, 300, "x")
    chunk_id = server.db.add_chunk(
        session_id, "/tmp/c1.mp3", "2026-01-05T10:00:00Z", "2026-01-05T10:05:00Z", 300.0, "sha1"
    )
    server.db.add_events([
        {"id": "e1", "session_id": session_id, "chunk_id": chunk_id, "kind": "speech",
         "start_offset": 0.0, "end_offset": 200.0, "confidence": 0.8, "text": "hello",
         "fingerprint": None, "metadata": {}},
    ])  # fmt: skip
    server.db.finish_session(session_id, "completed")
    server.store.update(run_id, session_id=session_id)
    return session_id


def test_agent_programming_route_returns_study_summary(server, no_real_worker):
    port = server.server_address[1]
    run_id = _start(server, no_real_worker)["run_id"]
    _attach_real_session(server, run_id)
    with _get(port, f"/agent/{run_id}/programming") as resp:
        body = json.loads(resp.read())
    assert body["run_id"] == run_id
    assert body["study_summary"]["content_block_count"] >= 1
    assert all("confidence" in b for b in body["content_blocks"])


def test_agent_export_route_runs_in_background_and_writes_a_real_xlsx(server, no_real_worker):
    import time

    port = server.server_address[1]
    run_id = _start(server, no_real_worker)["run_id"]
    _attach_real_session(server, run_id)

    with _get(port, f"/agent/{run_id}/export/xlsx", method="POST") as resp:
        assert resp.status == 202
        ack = json.loads(resp.read())
    assert ack["status"] == "started" and ack["run_id"] == run_id

    export_dir = Path(ack["export_dir"])
    deadline = time.monotonic() + 10
    journal: list[dict] = []
    while time.monotonic() < deadline:
        journal = server.store.events(run_id)
        if any(e["event_type"] in ("EXPORT_CREATED", "EXPORT_FAILED") for e in journal):
            break
        time.sleep(0.05)
    assert any(e["event_type"] == "EXPORT_CREATED" for e in journal), journal
    assert export_dir.is_dir() and list(export_dir.glob("*.xlsx"))


# ------------------------------------------------------------------------------ WebSocket
def test_agent_events_websocket_notifies_on_change_and_closes_on_terminal_state(
    server, no_real_worker
):
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    run_id = _start(server, no_real_worker)["run_id"]
    url = f"ws://127.0.0.1:{server.server_address[1]}/agent/{run_id}/events"
    with connect(url, additional_headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
        first = json.loads(ws.recv(timeout=5))
        assert first == {"event": "changed", "run_id": run_id, "state": "CREATED"}

        server.store.transition(run_id, AgentState.RESOLVING_STREAM)
        second = json.loads(ws.recv(timeout=5))
        assert second["state"] == "RESOLVING_STREAM"

        server.store.update(run_id, state="FAILED", error="boom")
        third = json.loads(ws.recv(timeout=5))
        assert third["state"] == "FAILED"

        with pytest.raises(ConnectionClosed):  # server sends a close frame once terminal
            ws.recv(timeout=5)


def test_agent_events_websocket_unknown_run_is_404(server):
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    url = f"ws://127.0.0.1:{server.server_address[1]}/agent/does-not-exist/events"
    with pytest.raises(InvalidStatus):
        connect(url, additional_headers={"Authorization": f"Bearer {TOKEN}"})
