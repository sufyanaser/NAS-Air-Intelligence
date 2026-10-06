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

from nas_air_intelligence.sidecar import SidecarServer, health_payload

TOKEN = "test-token"


def _get(port: int, path: str, token: str | None = TOKEN, method: str = "GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req, timeout=5)


@pytest.fixture
def server():
    srv = SidecarServer(TOKEN)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


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
