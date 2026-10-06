"""PyInstaller entry point for the NAS Air desktop sidecar."""

import os
import sys

from nas_air_intelligence.sidecar import main

if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # see nas_air_intelligence.sidecar: avoids a finalization crash on Windows
