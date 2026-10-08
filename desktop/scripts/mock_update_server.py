"""Lightweight HTTP server for local testing of Tauri v2 updater flows.

Serves latest.json and update artifacts from a specified directory.
Guarantees correct application/json Content-Type headers for .json files.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# Ensure .json is mapped to application/json
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("application/zip", ".zip")
mimetypes.add_type("text/plain", ".sig")


class UpdaterRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        return super().do_GET()


def run_server(directory: Path, port: int) -> None:
    os.chdir(str(directory))
    server = HTTPServer(("127.0.0.1", port), UpdaterRequestHandler)
    print(f"MOCK_UPDATER_SERVER_READY:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock Tauri Updater HTTP Server")
    parser.add_argument("--dir", required=True, type=Path, help="Directory to serve")
    parser.add_argument("--port", default=9444, type=int, help="Port to listen on")
    args = parser.parse_args()

    if not args.dir.exists():
        sys.exit(f"Directory not found: {args.dir}")

    run_server(args.dir.resolve(), args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
