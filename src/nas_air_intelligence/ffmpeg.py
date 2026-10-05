from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<value>-?\d+(?:\.\d+)?)")
SILENCE_END_RE = re.compile(r"silence_end:\s*(?P<value>-?\d+(?:\.\d+)?)")


class ToolMissingError(RuntimeError):
    pass


def find_binary(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolMissingError(f"required executable not found in PATH: {name}")
    return path


def probe_duration(path: str | Path, ffprobe: str = "ffprobe") -> float:
    executable = find_binary(ffprobe)
    proc = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    return float(payload["format"]["duration"])


def parse_silencedetect(stderr: str, duration: float) -> list[tuple[str, float, float]]:
    points: list[tuple[str, float]] = []
    for line in stderr.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            points.append(("start", max(0.0, float(start_match.group("value")))))
        end_match = SILENCE_END_RE.search(line)
        if end_match:
            points.append(("end", max(0.0, float(end_match.group("value")))))

    silences: list[tuple[float, float]] = []
    open_start: float | None = None
    for kind, value in points:
        if kind == "start":
            if open_start is None:
                open_start = min(value, duration)
        elif open_start is not None:
            silences.append((open_start, min(value, duration)))
            open_start = None
    if open_start is not None:
        silences.append((open_start, duration))

    intervals: list[tuple[str, float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start > cursor:
            intervals.append(("audio", cursor, start))
        if end > start:
            intervals.append(("silence", start, end))
        cursor = max(cursor, end)
    if cursor < duration:
        intervals.append(("audio", cursor, duration))
    if not intervals and duration > 0:
        intervals.append(("audio", 0.0, duration))
    return intervals


def silence_intervals(
    path: str | Path,
    duration: float,
    ffmpeg: str = "ffmpeg",
    noise_db: float = -38.0,
    minimum_silence: float = 1.0,
) -> list[tuple[str, float, float]]:
    executable = find_binary(ffmpeg)
    proc = subprocess.run(
        [
            executable,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={minimum_silence}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    return parse_silencedetect(proc.stderr, duration)
