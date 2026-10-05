from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .db import Database
from .ffmpeg import silence_intervals


class Analyzer(Protocol):
    def analyze(self, path: Path, duration: float) -> list[dict]: ...


class FfmpegSilenceAnalyzer:
    """Deterministic baseline: silence vs non-silent audio.

    It is intentionally not described as speech/music AI classification.
    """

    def __init__(self, ffmpeg: str = "ffmpeg") -> None:
        self.ffmpeg = ffmpeg

    def analyze(self, path: Path, duration: float) -> list[dict]:
        intervals = silence_intervals(path, duration, ffmpeg=self.ffmpeg)
        return [
            {
                "kind": kind,
                "start_offset": start,
                "end_offset": end,
                "confidence": 1.0,
                "label": "ffmpeg-silencedetect",
            }
            for kind, start, end in intervals
        ]


class InaSpeechMusicAnalyzer:
    """Optional ML adapter for music/speech/noise segmentation."""

    def __init__(self) -> None:
        try:
            from inaSpeechSegmenter import Segmenter
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "inaSpeechSegmenter is not installed; install the 'ml' optional dependencies"
            ) from exc
        self._segmenter = Segmenter()

    def analyze(self, path: Path, duration: float) -> list[dict]:
        output: list[dict] = []
        for label, start, end in self._segmenter(str(path)):
            mapped = {
                "male": "speech",
                "female": "speech",
                "music": "music",
                "noise": "noise",
                "noEnergy": "silence",
            }.get(label, "unknown")
            output.append(
                {
                    "kind": mapped,
                    "start_offset": max(0.0, float(start)),
                    "end_offset": min(duration, float(end)),
                    "confidence": None,
                    "label": str(label),
                }
            )
        return output


def analyze_pending_chunks(
    db: Database,
    session_id: str,
    analyzer: Analyzer,
) -> int:
    analyzed_count = 0
    for chunk in db.unanalyzed_chunks(session_id):
        events = analyzer.analyze(Path(chunk["path"]), float(chunk["duration_seconds"]))
        for event in events:
            event["session_id"] = session_id
            event["chunk_id"] = chunk["id"]
        db.add_events(events)
        db.mark_chunk_analyzed(chunk["id"])
        analyzed_count += 1
    return analyzed_count
