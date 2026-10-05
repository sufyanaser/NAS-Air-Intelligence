from __future__ import annotations

import logging
import math
import os
import site
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def ensure_cuda_dll_path() -> list[str]:
    """Ensure NVIDIA CUDA/cuDNN DLL directories in site-packages are accessible on Windows."""
    added: list[str] = []
    if os.name != "nt":
        return added

    search_dirs: list[str] = []
    try:
        for base in site.getsitepackages():
            for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
                cand = os.path.join(base, *sub.split("/"))
                if os.path.isdir(cand) and cand not in search_dirs:
                    search_dirs.append(cand)
    except Exception as exc:
        logger.debug("Failed checking site-packages for NVIDIA DLLs: %s", exc)

    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for directory in search_dirs:
        try:
            os.add_dll_directory(directory)
            added.append(directory)
        except (OSError, AttributeError):
            pass
        if directory not in path_dirs:
            os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
            path_dirs.insert(0, directory)

    return added


class TranscriptionError(RuntimeError):
    """Raised when audio transcription fails."""


@dataclass
class TranscriptionSegment:
    start: float
    end: float
    text: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptionResult:
    text: str
    language: str
    language_probability: float
    duration: float
    processing_time: float
    segments: list[TranscriptionSegment] = field(default_factory=list)
    model: str = "tiny"
    device: str = "cuda"
    compute_type: str = "float16"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "language_probability": round(self.language_probability, 4),
            "duration": round(self.duration, 3),
            "processing_time": round(self.processing_time, 3),
            "model": self.model,
            "device": self.device,
            "compute_type": self.compute_type,
            "segments": [s.to_dict() for s in self.segments],
        }


@dataclass
class TranscriptionConfig:
    model_size_or_path: str = "tiny"
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = "ar"
    beam_size: int = 5
    initial_prompt: str | None = None
    vad_filter: bool = False
    download_root: str | Path | None = None
    allow_cpu_fallback: bool = True


class SpeechTranscriber:
    """Production speech transcriber using faster-whisper with GPU/CPU support."""

    def __init__(self, config: TranscriptionConfig | None = None) -> None:
        self.config = config or TranscriptionConfig()
        self._model = None
        self._resolved_device: str | None = None
        self._resolved_compute_type: str | None = None

    def _resolve_device_and_compute(self) -> tuple[str, str]:
        requested_device = self.config.device
        requested_compute = self.config.compute_type

        if requested_device == "auto":
            try:
                import ctranslate2

                cuda_available = ctranslate2.get_cuda_device_count() > 0
            except Exception:
                cuda_available = False

            device = "cuda" if cuda_available else "cpu"
        else:
            device = requested_device

        if requested_compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        else:
            compute = requested_compute

        return device, compute

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        ensure_cuda_dll_path()

        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed; install the 'ml' optional dependencies"
            ) from exc

        device, compute_type = self._resolve_device_and_compute()

        try:
            download_root = (
                str(self.config.download_root) if self.config.download_root is not None else None
            )
            model = WhisperModel(
                self.config.model_size_or_path,
                device=device,
                compute_type=compute_type,
                download_root=download_root,
            )
            self._resolved_device = device
            self._resolved_compute_type = compute_type
            self._model = model
            return model
        except Exception as exc:
            if device == "cuda" and self.config.allow_cpu_fallback:
                logger.warning(
                    "Failed loading Whisper on CUDA (%s). Falling back to CPU...",
                    exc,
                )
                try:
                    cpu_compute = "int8" if self.config.compute_type == "auto" else "float32"
                    model = WhisperModel(
                        self.config.model_size_or_path,
                        device="cpu",
                        compute_type=cpu_compute,
                        download_root=download_root,
                    )
                    self._resolved_device = "cpu"
                    self._resolved_compute_type = cpu_compute
                    self._model = model
                    return model
                except Exception as cpu_exc:
                    msg = f"Whisper model CPU fallback failed: {cpu_exc}"
                    raise TranscriptionError(msg) from cpu_exc
            raise TranscriptionError(f"Failed loading Whisper model: {exc}") from exc

    @property
    def model_name(self) -> str:
        return self.config.model_size_or_path

    @property
    def device(self) -> str:
        if self._resolved_device is None:
            self._resolve_device_and_compute()
        return self._resolved_device or self.config.device

    @property
    def compute_type(self) -> str:
        if self._resolved_compute_type is None:
            self._resolve_device_and_compute()
        return self._resolved_compute_type or self.config.compute_type

    def transcribe(
        self,
        audio_path: str | Path,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file into timestamped structured segments."""
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")
        if path.stat().st_size == 0:
            raise ValueError(f"Audio file is empty: {path}")

        model = self._load_model()
        target_lang = language if language is not None else self.config.language

        start_time = time.monotonic()
        try:
            segments_iter, info = model.transcribe(
                str(path),
                language=target_lang,
                beam_size=self.config.beam_size,
                initial_prompt=self.config.initial_prompt,
                vad_filter=self.config.vad_filter,
            )

            segments: list[TranscriptionSegment] = []
            for seg in segments_iter:
                # Confidence approximated from avg_logprob
                confidence = None
                if seg.avg_logprob is not None:
                    try:
                        confidence = round(max(0.0, min(1.0, math.exp(seg.avg_logprob))), 4)
                    except OverflowError:
                        confidence = 0.0

                avg_lp = (
                    round(float(seg.avg_logprob), 4)
                    if seg.avg_logprob is not None
                    else None
                )
                no_speech_p = (
                    round(float(seg.no_speech_prob), 4)
                    if seg.no_speech_prob is not None
                    else None
                )
                segments.append(
                    TranscriptionSegment(
                        start=round(float(seg.start), 3),
                        end=round(float(seg.end), 3),
                        text=seg.text.strip(),
                        avg_logprob=avg_lp,
                        no_speech_prob=no_speech_p,
                        confidence=confidence,
                    )
                )

            full_text = " ".join(s.text for s in segments if s.text).strip()
            processing_time = time.monotonic() - start_time
            lang_prob = (
                float(info.language_probability)
                if info.language_probability is not None
                else 1.0
            )

            return TranscriptionResult(
                text=full_text,
                language=info.language or target_lang or "unknown",
                language_probability=lang_prob,
                duration=float(info.duration) if info.duration is not None else 0.0,
                processing_time=processing_time,
                segments=segments,
                model=self.config.model_size_or_path,
                device=self._resolved_device or "unknown",
                compute_type=self._resolved_compute_type or "unknown",
            )
        except Exception as exc:
            raise TranscriptionError(f"Transcription failed for {path.name}: {exc}") from exc

    def transcribe_safe(
        self,
        audio_path: str | Path,
        language: str | None = None,
    ) -> tuple[TranscriptionResult | None, str | None]:
        """Safely transcribe without crashing on segment failure."""
        try:
            result = self.transcribe(audio_path, language=language)
            return result, None
        except Exception as exc:
            logger.error("Safe transcription caught error for %s: %s", audio_path, exc)
            return None, str(exc)
