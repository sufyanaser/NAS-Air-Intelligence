"""Local sidecar for the NAS Air desktop shell.

The desktop app (Tauri) starts this process hidden, hands it a one-time bearer token through
``NAS_AIR_SIDECAR_TOKEN`` and reads one JSON "ready" line from stdout. The server binds to the
loopback interface only and every request must carry the token.

Lifetime: the sidecar exits when ``POST /shutdown`` is called, or when its stdin is closed -
which is what happens when the parent app exits or crashes - so it cannot outlive the desktop.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import platform
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import __version__

TOKEN_ENV = "NAS_AIR_SIDECAR_TOKEN"
LOOPBACK = "127.0.0.1"


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


class SidecarServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, token: str, port: int = 0) -> None:
        if not token:
            raise ValueError("a non-empty token is required")
        self.token = token
        super().__init__((LOOPBACK, port), _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: SidecarServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # stdout is reserved for the ready line

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        return hmac.compare_digest(supplied.encode(), expected.encode())

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
        elif self.path == "/health":
            self._send(200, health_payload())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
        elif self.path == "/shutdown":
            self._send(200, {"status": "shutting_down"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, {"error": "not found"})


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
    parser.add_argument(
        "--no-stdin-watch", action="store_true", help="do not exit when stdin closes (debugging)"
    )
    args = parser.parse_args(argv)

    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        print(f"error: {TOKEN_ENV} is not set", file=sys.stderr)
        return 2

    server = SidecarServer(token, args.port)
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
