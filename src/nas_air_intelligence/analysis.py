from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from .db import Database
from .ffmpeg import acoustic_fingerprint, silence_intervals

logger = logging.getLogger(__name__)


class Analyzer(Protocol):
    def analyze(self, path: Path, duration: float) -> list[dict[str, Any]]: ...


class FfmpegSilenceAnalyzer:
    """Deterministic baseline: silence vs non-silent audio.

    It is intentionally not described as speech/music AI classification.
    """

    def __init__(self, ffmpeg: str = "ffmpeg") -> None:
        self.ffmpeg = ffmpeg

    def analyze(self, path: Path, duration: float) -> list[dict[str, Any]]:
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


class WhisperSpeechAnalyzer:
    """Timeline analyzer integrating silence detection, Whisper speech transcription,

    and acoustic fingerprinting.

    Classification policy:
    - speech: detected speech segments with transcription text and confidence.
    - silence: verified silence intervals from ffmpeg silencedetect.
    - unknown: non-silent intervals where speech is absent or evidence is insufficient.
    """

    def __init__(
        self,
        transcriber: Any | None = None,
        ffmpeg: str = "ffmpeg",
        enable_fingerprinting: bool = True,
    ) -> None:
        if transcriber is None:
            from .transcription import SpeechTranscriber

            transcriber = SpeechTranscriber()
        self.transcriber = transcriber
        self.ffmpeg = ffmpeg
        self.enable_fingerprinting = enable_fingerprinting

    def analyze(self, path: Path, duration: float) -> list[dict[str, Any]]:
        # 1. Run baseline silence detection
        silence_list = silence_intervals(path, duration, ffmpeg=self.ffmpeg)

        # 2. Extract acoustic fingerprint for the chunk
        fp = (
            acoustic_fingerprint(path, ffmpeg=self.ffmpeg, max_duration=120.0)
            if self.enable_fingerprinting
            else None
        )

        # 3. Transcribe audio safely
        result, error = self.transcriber.transcribe_safe(path)

        events: list[dict[str, Any]] = []

        if error:
            # Transcription failure recorded as structured unknown event with incident status
            events.append(
                {
                    "kind": "unknown",
                    "start_offset": 0.0,
                    "end_offset": duration,
                    "confidence": None,
                    "label": "transcription-failure",
                    "text": None,
                    "fingerprint": fp,
                    "metadata": {
                        "error": error,
                        "status": "incident",
                    },
                }
            )
            return events

        if result and result.segments:
            for seg in result.segments:
                is_speech = bool(
                    seg.text and (seg.no_speech_prob is None or seg.no_speech_prob < 0.6)
                )
                events.append(
                    {
                        "kind": "speech" if is_speech else "unknown",
                        "start_offset": max(0.0, min(duration, seg.start)),
                        "end_offset": max(0.0, min(duration, seg.end)),
                        "confidence": seg.confidence,
                        "label": "faster-whisper",
                        "text": seg.text if is_speech else None,
                        "fingerprint": fp,
                        "metadata": {
                            "language": result.language,
                            "language_probability": result.language_probability,
                            "avg_logprob": seg.avg_logprob,
                            "no_speech_prob": seg.no_speech_prob,
                            "model": result.model,
                            "device": result.device,
                            "compute_type": result.compute_type,
                        },
                    }
                )

        # Preserve silence intervals from ffmpeg silencedetect
        for kind, s_start, s_end in silence_list:
            if kind == "silence" and (s_end - s_start) >= 1.0:
                events.append(
                    {
                        "kind": "silence",
                        "start_offset": s_start,
                        "end_offset": s_end,
                        "confidence": 1.0,
                        "label": "ffmpeg-silencedetect",
                        "text": None,
                        "fingerprint": None,
                        "metadata": {},
                    }
                )

        if not events:
            events.append(
                {
                    "kind": "unknown",
                    "start_offset": 0.0,
                    "end_offset": duration,
                    "confidence": None,
                    "label": "no-events-detected",
                    "text": None,
                    "fingerprint": fp,
                    "metadata": {
                        "language": result.language if result else None,
                    },
                }
            )

        events.sort(key=lambda e: e["start_offset"])
        return events


class InaSpeechMusicAnalyzer:
    """Optional ML adapter for music/speech/noise segmentation."""

    def __init__(self) -> None:
        try:
            from inaSpeechSegmenter import Segmenter
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "inaSpeechSegmenter is not installed; install the 'ina' optional dependencies"
            ) from exc
        self._segmenter = Segmenter()

    def analyze(self, path: Path, duration: float) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
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


def analyze_chunk(
    db: Database,
    session_id: str,
    chunk: dict[str, Any],
    analyzer: Analyzer,
) -> None:
    chunk_path = Path(chunk["path"])
    chunk_duration = float(chunk["duration_seconds"])
    try:
        events = analyzer.analyze(chunk_path, chunk_duration)
    except Exception as exc:
        logger.error("Analysis crashed on chunk %s: %s", chunk["id"], exc)
        db.add_incident(
            session_id,
            "analysis_failure",
            f"chunk_id={chunk['id']} path={chunk_path.name} error={exc}",
        )
        events = [
            {
                "kind": "unknown",
                "start_offset": 0.0,
                "end_offset": chunk_duration,
                "confidence": None,
                "label": "analyzer-crashed",
                "text": None,
                "fingerprint": None,
                "metadata": {"error": str(exc)},
            }
        ]

    for event in events:
        event["session_id"] = session_id
        event["chunk_id"] = chunk["id"]
        if event.get("metadata", {}).get("status") == "incident":
            error_detail = event.get("metadata", {}).get("error", "unknown error")
            db.add_incident(
                session_id,
                "transcription_incident",
                f"chunk_id={chunk['id']} error={error_detail}",
            )

    db.add_events(events)
    db.mark_chunk_analyzed(chunk["id"])


def analyze_pending_chunks(
    db: Database,
    session_id: str,
    analyzer: Analyzer,
) -> int:
    analyzed_count = 0
    for chunk in db.unanalyzed_chunks(session_id):
        analyze_chunk(db, session_id, chunk, analyzer)
        analyzed_count += 1
    return analyzed_count