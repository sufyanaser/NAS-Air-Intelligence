"""Cross-platform subprocess helpers enforcing zero-console execution on Windows.

On Windows, when a GUI application or a background process invokes `subprocess.run`
or `subprocess.Popen` without `CREATE_NO_WINDOW`, Windows may allocate a console
window (flashing a black command prompt or conhost window).

These helpers guarantee that every child process runs completely silent without
creating any console window.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

# Windows creation flag: 0x08000000 (CREATE_NO_WINDOW)
CREATE_NO_WINDOW: int = 0x0800_0000 if sys.platform == "win32" else 0


def silent_creation_flags(extra_flags: int = 0) -> int:
    """Return creationflags including CREATE_NO_WINDOW on Windows."""
    return CREATE_NO_WINDOW | extra_flags


def run_silent(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Execute subprocess.run with CREATE_NO_WINDOW injected on Windows."""
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
    return subprocess.run(*args, **kwargs)


def popen_silent(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
    """Spawn subprocess.Popen with CREATE_NO_WINDOW injected on Windows."""
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
    return subprocess.Popen(*args, **kwargs)
