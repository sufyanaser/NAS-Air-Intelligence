"""PyInstaller entry point for the NAS Air desktop sidecar.

A frozen onefile build has no separate ``python.exe`` next to it, so this one executable must
also serve as the agent worker the sidecar spawns. The sidecar's own flags (``--port``,
``--storage``, ``--db``, ...) are themselves ordinary leading arguments, so "any argv means
CLI" is not a safe dispatch rule - it would also hijack a sidecar started with ``--storage``.
Instead the frozen worker command (``agent.launcher.spawn_worker``'s frozen-mode branch) is
prefixed with an explicit ``cli`` sentinel that is never a valid leading sidecar flag:

    nas-air-sidecar.exe                              -> sidecar HTTP server
    nas-air-sidecar.exe --storage ... --port ...      -> sidecar HTTP server, with those flags
    nas-air-sidecar.exe cli --db ... agent _run ...   -> regular CLI (the "cli" token is stripped)
"""

import os
import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from nas_air_intelligence.cli import main as cli_main

        return cli_main(sys.argv[2:])
    from nas_air_intelligence.sidecar import main as sidecar_main

    return sidecar_main(sys.argv[1:])


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # see nas_air_intelligence.sidecar: avoids a finalization crash on Windows
