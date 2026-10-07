"""Local sidecar for the NAS Air desktop shell.

The desktop app (Tauri) starts this process hidden, hands it a one-time bearer token through
``NAS_AIR_SIDECAR_TOKEN`` and reads one JSON "ready" line from stdout. The server binds to the
loopback interface only and every request must carry the token.

Lifetime: the sidecar exits when ``POST /shutdown`` is called, or when its stdin is closed -
which is what happens when the parent app exits or crashes - so it cannot outlive the desktop.

Desktop-to-Core Contract (Phase2.md section 11) - kept minimal, backed by the same SQLite
state the CLI uses, so closing the desktop or losing this sidecar never stops a monitoring run:

    GET  /health
    POST /agent/start
    GET  /agent/{id}/status
    POST /agent/{id}/stop
    GET  /agent/{id}/result
    GET  /agent/{id}/timeline
    GET  /agent/{id}/journal     (Agent Run Journal - the desktop activity feed)
    GET  /agent/{id}/programming (Phase 5: content blocks, candidates, clock patterns, dayparts)
    POST /agent/{id}/export/xlsx (Phase 6: 8-sheet study workbook; runs in a background thread)
    WS   /agent/{id}/events      notification-only; persistent backend state is authoritative
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import platform
import re
import shutil
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .agent.excel_export import write_workbook
from .agent.launcher import (
    create_and_launch_run,
    describe,
    live_timeline,
    request_stop,
)
from .agent.programming import build_programming_analysis
from .agent.store import AgentStore
from .db import Database

logger = logging.getLogger(__name__)

TOKEN_ENV = "NAS_AIR_SIDECAR_TOKEN"
LOOPBACK = "127.0.0.1"
_RUN_ID = r"[0-9a-fA-F-]{6,36}"
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455 section 1.3


def health_payload() -> dict[str, Any]:
    """Readiness report of the Python core: what the desktop needs before it can start a run."""
    return {
        "status": "ready",
        "service": "nas-air-sidecar",
        "version": __version__,
        "python": platform.python_version(),
        "pid": os.getpid(),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
    }


def default_storage_dir() -> Path:
    """Where the desktop build keeps its data when no explicit override is given.

    The CLI defaults to ``./data`` (fine for a terminal invoked from the repo). A frozen
    desktop sidecar has no meaningful "current directory" to anchor that to, so it defaults
    to a per-user application-data folder instead.
    """
    override = os.environ.get("NAS_AIR_STORAGE")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "NAS Air Intelligence"


def default_db_path(storage: Path) -> Path:
    override = os.environ.get("NAS_AIR_DB")
    return Path(override) if override else storage / "nas-air.db"


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class SidecarServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        token: str,
        port: int = 0,
        *,
        storage: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        if not token:
            raise ValueError("a non-empty token is required")
        self.token = token
        storage = storage or default_storage_dir()
        db_path = db_path or default_db_path(storage)
        storage.mkdir(parents=True, exist_ok=True)
        self.storage = str(storage.resolve())
        self.db_path = str(db_path.resolve())
        self.log_dir = storage / "logs"
        self.db = Database(self.db_path)
        self.store = AgentStore(self.db)
        super().__init__((LOOPBACK, port), _Handler)


# ---------------------------------------------------------------------------- HTTP routes
def _run_or_404(server: SidecarServer, ident: str) -> dict[str, Any]:
    run = server.store.find(ident)
    if not run:
        raise ApiError(404, f"unknown agent run: {ident}")
    return run


def _route_health(handler: _Handler, _match: re.Match[str]) -> None:
    handler._send(200, health_payload())


def _route_shutdown(handler: _Handler, _match: re.Match[str]) -> None:
    handler._send(200, {"status": "shutting_down"})
    threading.Thread(target=handler.server.shutdown, daemon=True).start()


def _route_agent_start(handler: _Handler, _match: re.Match[str]) -> None:
    body = handler._read_json_body()
    server = handler.server
    try:
        result = create_and_launch_run(
            server.store, db_path=server.db_path, storage=server.storage, log_dir=server.log_dir,
            station=body.get("station") or body.get("name"), page=body.get("page"),
            url=body.get("url"), mode=body.get("mode"), duration=body.get("duration"),
            segment_seconds=int(body.get("segment_seconds", 60)),
            analyzer=body.get("analyzer", "whisper"), model=body.get("model"),
        )  # fmt: skip
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc
    handler._send(200, {**result, "next": f"/agent/{result['run_id']}/status"})


def _route_agent_status(handler: _Handler, match: re.Match[str]) -> None:
    server = handler.server
    run = _run_or_404(server, match.group(1))
    handler._send(200, describe(server.db, server.store, run))


def _route_agent_stop(handler: _Handler, match: re.Match[str]) -> None:
    server = handler.server
    run = _run_or_404(server, match.group(1))
    handler._send(
        200,
        request_stop(
            server.db, server.store, run, db_path=server.db_path, storage=server.storage,
            log_dir=server.log_dir,
        ),  # fmt: skip
    )


def _route_agent_result(handler: _Handler, match: re.Match[str]) -> None:
    server = handler.server
    run = _run_or_404(server, match.group(1))
    info = describe(server.db, server.store, run)
    if not run["is_terminal"]:
        handler._send(409, {**info, "note": "run has not finished; no final result yet"})
        return
    handler._send(200, info)


def _route_agent_timeline(handler: _Handler, match: re.Match[str]) -> None:
    server = handler.server
    run = _run_or_404(server, match.group(1))
    handler._send(
        200, {"run_id": run["id"], "session_id": run["session_id"],
              **live_timeline(server.db, run["session_id"])},  # fmt: skip
    )


def _route_agent_journal(handler: _Handler, match: re.Match[str]) -> None:
    server = handler.server
    run = _run_or_404(server, match.group(1))
    handler._send(200, {"run_id": run["id"], "events": server.store.events(run["id"])})


def _route_agent_programming(handler: _Handler, match: re.Match[str]) -> None:
    server = handler.server
    run = _run_or_404(server, match.group(1))
    if not run.get("session_id"):
        raise ApiError(409, "run has not started capturing yet")
    analysis = build_programming_analysis(server.db, run["session_id"])
    handler._send(200, {"run_id": run["id"], **analysis})


def _route_agent_export(handler: _Handler, match: re.Match[str]) -> None:
    """Kicks off the xlsx export in a background thread and returns immediately (Phase2.md
    section 20: "Excel generation runs in the background"). Completion and failure both show
    up as an Agent Run Journal entry - the desktop polls/watches that, not this response."""
    server = handler.server
    run = _run_or_404(server, match.group(1))
    if not run.get("session_id"):
        raise ApiError(409, "run has not started capturing yet")
    export_dir = Path(server.storage) / "exports"

    def _do_export() -> None:
        try:
            path = write_workbook(
                server.db, run["session_id"], export_dir,
                requested_seconds=float(run["duration_seconds"]), run=run,
            )  # fmt: skip
            server.store.log_event(
                run["id"], stage=run["state"], event_type="EXPORT_CREATED",
                message=str(path), details={"xlsx": str(path)},
            )  # fmt: skip
        except Exception as exc:
            logger.exception("excel export failed for run %s", run["id"])
            server.store.log_event(
                run["id"], stage=run["state"], event_type="EXPORT_FAILED",
                status="failed", severity="error", message=str(exc),
            )  # fmt: skip

    threading.Thread(target=_do_export, daemon=True, name="xlsx-export").start()
    handler._send(202, {"run_id": run["id"], "status": "started", "export_dir": str(export_dir)})


_GET_ROUTES: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"^/health$"), _route_health),
    (re.compile(rf"^/agent/({_RUN_ID})/status$"), _route_agent_status),
    (re.compile(rf"^/agent/({_RUN_ID})/result$"), _route_agent_result),
    (re.compile(rf"^/agent/({_RUN_ID})/timeline$"), _route_agent_timeline),
    (re.compile(rf"^/agent/({_RUN_ID})/journal$"), _route_agent_journal),
    (re.compile(rf"^/agent/({_RUN_ID})/programming$"), _route_agent_programming),
]
_WS_ROUTES: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(rf"^/agent/({_RUN_ID})/events$"), None),  # handled by _maybe_handle_ws_upgrade
]
_POST_ROUTES: list[tuple[re.Pattern[str], Any]] = [
    (re.compile(r"^/shutdown$"), _route_shutdown),
    (re.compile(r"^/agent/start$"), _route_agent_start),
    (re.compile(rf"^/agent/({_RUN_ID})/stop$"), _route_agent_stop),
    (re.compile(rf"^/agent/({_RUN_ID})/export/xlsx$"), _route_agent_export),
]


class _Handler(BaseHTTPRequestHandler):
    server: SidecarServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # stdout is reserved for the ready line

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(400, f"invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "JSON body must be an object")
        return payload

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        return hmac.compare_digest(supplied.encode(), expected.encode())

    def _dispatch(self, routes: list[tuple[re.Pattern[str], Any]], path: str) -> bool:
        for pattern, handler_fn in routes:
            match = pattern.match(path)
            if not match:
                continue
            try:
                handler_fn(self, match)
            except ApiError as exc:
                self._send(exc.status, {"error": exc.message})
            except Exception:
                logger.exception("%s %s failed", self.command, path)
                self._send(500, {"error": "internal error"})
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = self.path.split("?", 1)[0]
        if _maybe_handle_ws_upgrade(self, path):
            return
        if not self._dispatch(_GET_ROUTES, path):
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = self.path.split("?", 1)[0]
        if not self._dispatch(_POST_ROUTES, path):
            self._send(404, {"error": "not found"})


# ---------------------------------------------------------------------------- WebSocket
# WebSocket is notification only (Phase2.md section 11): a frame just means "state changed,
# re-fetch the REST endpoints". Persistent SQLite state remains authoritative, so a dropped
# or never-opened socket cannot hide or fake a run's real progress.
def _ws_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()  # noqa: S324 - WS handshake, not a security hash
    return base64.b64encode(digest).decode("ascii")


def _ws_send_text(conn: Any, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])  # FIN + text frame opcode
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    conn.sendall(bytes(header) + payload)


def _ws_send_close(conn: Any) -> None:
    with contextlib.suppress(OSError):
        conn.sendall(b"\x88\x00")


def _recv_exact(conn: Any, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def _ws_read_frame(conn: Any) -> tuple[int, bytes] | None:
    head = _recv_exact(conn, 2)
    if head is None:
        return None
    b0, b1 = head
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        ext = _recv_exact(conn, 2)
        if ext is None:
            return None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = _recv_exact(conn, 8)
        if ext is None:
            return None
        length = struct.unpack(">Q", ext)[0]
    mask = _recv_exact(conn, 4) if masked else b""
    if masked and mask is None:
        return None
    data = _recv_exact(conn, length) if length else b""
    if data is None:
        return None
    if masked and mask:
        data = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
    return opcode, data


def _maybe_handle_ws_upgrade(handler: _Handler, path: str) -> bool:
    match = None
    for pattern, _ in _WS_ROUTES:
        match = pattern.match(path)
        if match:
            break
    if not match:
        return False
    if handler.headers.get("Upgrade", "").lower() != "websocket":
        handler._send(400, {"error": "expected a WebSocket upgrade"})
        return True
    key = handler.headers.get("Sec-WebSocket-Key")
    if not key:
        handler._send(400, {"error": "missing Sec-WebSocket-Key"})
        return True
    run_id = match.group(1)
    server = handler.server
    if not server.store.find(run_id):
        handler._send(404, {"error": f"unknown agent run: {run_id}"})
        return True

    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", _ws_accept_key(key))
    handler.end_headers()
    handler.close_connection = True  # we take the raw socket over; no further HTTP on it

    _run_ws_notification_loop(handler.connection, server, run_id)
    return True


def _run_ws_notification_loop(conn: Any, server: SidecarServer, run_id: str) -> None:
    conn.settimeout(1.0)
    last_signature: tuple[str, str] | None = None
    try:
        while True:
            run = server.store.find(run_id)
            if run is None:
                break
            signature = (run["state"], run["updated_at"])
            if signature != last_signature:
                last_signature = signature
                message = {"event": "changed", "run_id": run["id"], "state": run["state"]}
                _ws_send_text(conn, json.dumps(message))
            if run["is_terminal"]:
                _ws_send_close(conn)
                break
            try:
                frame = _ws_read_frame(conn)
            except TimeoutError:
                continue
            except OSError:
                break
            if frame is None:
                break
            if frame[0] == 0x8:  # client close
                _ws_send_close(conn)
                break
    except (OSError, BrokenPipeError):
        pass
    finally:
        with contextlib.suppress(OSError):
            conn.close()


# ---------------------------------------------------------------------------- lifecycle
def _exit_when_stdin_closes(server: SidecarServer) -> None:
    """The parent holds our stdin open; EOF means the parent is gone."""
    try:
        while sys.stdin.buffer.read(1024):
            pass
    except (OSError, ValueError):
        pass
    server.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nas-air-sidecar")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--storage", help="overrides NAS_AIR_STORAGE / the per-user data dir")
    parser.add_argument("--db", help="overrides NAS_AIR_DB / <storage>/nas-air.db")
    parser.add_argument(
        "--no-stdin-watch", action="store_true", help="do not exit when stdin closes (debugging)"
    )
    args = parser.parse_args(argv)

    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        print(f"error: {TOKEN_ENV} is not set", file=sys.stderr)
        return 2

    storage = Path(args.storage) if args.storage else None
    db_path = Path(args.db) if args.db else None
    server = SidecarServer(token, args.port, storage=storage, db_path=db_path)
    if not args.no_stdin_watch:
        threading.Thread(
            target=_exit_when_stdin_closes, args=(server,), daemon=True, name="stdin-watch"
        ).start()
    print(json.dumps({"event": "ready", "port": server.server_address[1], "pid": os.getpid()}),
          flush=True)  # fmt: skip
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    code = main()
    # The stdin watcher is blocked in a read; on Windows letting the interpreter finalize
    # around it ends in an access violation, so leave without finalization once we are done.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
