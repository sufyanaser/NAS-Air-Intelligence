from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

_DURATION_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>s|m|h)?$", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_duration(value: str) -> float:
    match = _DURATION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("duration must look like 90s, 15m, 24h, or a plain second value")
    amount = float(match.group("value"))
    unit = (match.group("unit") or "s").lower()
    multiplier = {"s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    seconds = amount * multiplier
    if seconds <= 0:
        raise ValueError("duration must be greater than zero")
    return seconds


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()
