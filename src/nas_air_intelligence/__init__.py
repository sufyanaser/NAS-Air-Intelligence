from __future__ import annotations

from .analysis import FfmpegSilenceAnalyzer, WhisperSpeechAnalyzer
from .db import Database
from .recorder import StreamMonitor
from .transcription import SpeechTranscriber, TranscriptionConfig

__version__ = "0.1.0"

__all__ = [
    "Database",
    "FfmpegSilenceAnalyzer",
    "SpeechTranscriber",
    "StreamMonitor",
    "TranscriptionConfig",
    "WhisperSpeechAnalyzer",
    "__version__",
]
