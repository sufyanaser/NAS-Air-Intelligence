"""Preflight verification engineer for NAS Air Intelligence.

Tests operational readiness before any capture or monitoring campaign starts.
In CAPTURE_AND_ANALYSIS mode, any failure in required analysis dependencies
strictly blocks the monitoring session from starting.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..db import Database
from .resolver import StreamResolver

logger = logging.getLogger(__name__)

MIN_FREE_DISK_BYTES = 500 * 1024 * 1024  # 500 MiB


class PreflightMode(StrEnum):
    CAPTURE_ONLY = "CAPTURE_ONLY"
    CAPTURE_AND_ANALYSIS = "CAPTURE_AND_ANALYSIS"


class PreflightCheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class PreflightReasonCode(StrEnum):
    STREAM_UNRESOLVABLE = "STREAM_UNRESOLVABLE"
    FFMPEG_MISSING = "FFMPEG_MISSING"
    FFPROBE_MISSING = "FFPROBE_MISSING"
    STORAGE_INSUFFICIENT = "STORAGE_INSUFFICIENT"
    DATABASE_INACCESSIBLE = "DATABASE_INACCESSIBLE"
    TRANSCRIPTION_ENGINE_MISSING = "TRANSCRIPTION_ENGINE_MISSING"
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
    DEVICE_UNAVAILABLE = "DEVICE_UNAVAILABLE"
    COMPUTE_TYPE_UNSUPPORTED = "COMPUTE_TYPE_UNSUPPORTED"
    SAMPLE_TRANSCRIPTION_FAILED = "SAMPLE_TRANSCRIPTION_FAILED"


@dataclass
class PreflightCheckResult:
    check_name: str
    status: PreflightCheckStatus
    detail: str
    reason_code: PreflightReasonCode | str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["reason_code"] = (
            self.reason_code.value
            if isinstance(self.reason_code, PreflightReasonCode)
            else self.reason_code
        )
        return data


@dataclass
class PreflightReport:
    mode: PreflightMode
    passed: bool
    blocked: bool
    checks: list[PreflightCheckResult]
    failures: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "passed": self.passed,
            "blocked": self.blocked,
            "checks": [c.to_dict() for c in self.checks],
            "failures": self.failures,
            "reason_codes": self.reason_codes,
        }


class PreflightError(RuntimeError):
    """Raised when preflight verification fails in CAPTURE_AND_ANALYSIS mode."""


def generate_short_test_wav(duration_seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate a valid 16-bit PCM mono WAV audio file in memory."""
    buf = io.BytesIO()
    num_samples = int(duration_seconds * sample_rate)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # 16-bit PCM silence/tiny signal
        raw_data = struct.pack(f"<{num_samples}h", *(0 for _ in range(num_samples)))
        wf.writeframes(raw_data)
    return buf.getvalue()


class PreflightChecker:
    def __init__(
        self,
        *,
        storage_dir: str | Path = "data",
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        db: Database | None = None,
        resolver: StreamResolver | None = None,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.db = db
        self.resolver = resolver or StreamResolver(ffmpeg=ffmpeg, ffprobe=ffprobe)

    def run_preflight(
        self,
        mode: PreflightMode = PreflightMode.CAPTURE_AND_ANALYSIS,
        *,
        station: str | None = None,
        page: str | None = None,
        url: str | None = None,
        analyzer: str = "whisper",
        model: str | None = "tiny",
    ) -> PreflightReport:
        checks: list[PreflightCheckResult] = []
        failures: list[str] = []
        reason_codes: list[str] = []

        # 1. FFmpeg
        ffmpeg_path = shutil.which(self.ffmpeg)
        if ffmpeg_path:
            try:
                res = subprocess.run(
                    [self.ffmpeg, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0:
                    first_line = res.stdout.splitlines()[0] if res.stdout else "available"
                    checks.append(
                        PreflightCheckResult("ffmpeg", PreflightCheckStatus.PASS, first_line)
                    )
                else:
                    msg = f"ffmpeg returned exit code {res.returncode}"
                    checks.append(
                        PreflightCheckResult(
                            "ffmpeg",
                            PreflightCheckStatus.FAIL,
                            msg,
                            reason_code=PreflightReasonCode.FFMPEG_MISSING.value,
                        )
                    )
                    failures.append(msg)
                    reason_codes.append(PreflightReasonCode.FFMPEG_MISSING.value)
            except Exception as exc:
                msg = f"ffmpeg execution failed: {exc}"
                checks.append(
                    PreflightCheckResult(
                        "ffmpeg",
                        PreflightCheckStatus.FAIL,
                        msg,
                        reason_code=PreflightReasonCode.FFMPEG_MISSING.value,
                    )
                )
                failures.append(msg)
                reason_codes.append(PreflightReasonCode.FFMPEG_MISSING.value)
        else:
            msg = f"ffmpeg executable not found in PATH ({self.ffmpeg})"
            checks.append(
                PreflightCheckResult(
                    "ffmpeg",
                    PreflightCheckStatus.FAIL,
                    msg,
                    reason_code=PreflightReasonCode.FFMPEG_MISSING.value,
                )
            )
            failures.append(msg)
            reason_codes.append(PreflightReasonCode.FFMPEG_MISSING.value)

        # 2. FFprobe
        ffprobe_path = shutil.which(self.ffprobe)
        if ffprobe_path:
            checks.append(
                PreflightCheckResult(
                    "ffprobe", PreflightCheckStatus.PASS, f"found at {ffprobe_path}"
                )
            )
        else:
            msg = f"ffprobe executable not found in PATH ({self.ffprobe})"
            checks.append(
                PreflightCheckResult(
                    "ffprobe",
                    PreflightCheckStatus.FAIL,
                    msg,
                    reason_code=PreflightReasonCode.FFPROBE_MISSING.value,
                )
            )
            failures.append(msg)
            reason_codes.append(PreflightReasonCode.FFPROBE_MISSING.value)

        # 3. Storage
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            test_file = self.storage_dir / ".preflight_write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            free_bytes = shutil.disk_usage(self.storage_dir).free
            free_mib = free_bytes / (1024 * 1024)
            if free_bytes < MIN_FREE_DISK_BYTES:
                msg = f"Low disk space: {free_mib:.0f} MiB free (requires >= 500 MiB)"
                checks.append(
                    PreflightCheckResult(
                        "storage",
                        PreflightCheckStatus.FAIL,
                        msg,
                        reason_code=PreflightReasonCode.STORAGE_INSUFFICIENT.value,
                    )
                )
                failures.append(msg)
                reason_codes.append(PreflightReasonCode.STORAGE_INSUFFICIENT.value)
            else:
                checks.append(
                    PreflightCheckResult(
                        "storage",
                        PreflightCheckStatus.PASS,
                        f"writable, {free_mib:.0f} MiB free",
                        metrics={"free_bytes": free_bytes},
                    )
                )
        except Exception as exc:
            msg = f"storage verification failed for {self.storage_dir}: {exc}"
            checks.append(
                PreflightCheckResult(
                    "storage",
                    PreflightCheckStatus.FAIL,
                    msg,
                    reason_code=PreflightReasonCode.STORAGE_INSUFFICIENT.value,
                )
            )
            failures.append(msg)
            reason_codes.append(PreflightReasonCode.STORAGE_INSUFFICIENT.value)

        # 4. Database
        if self.db is not None:
            try:
                self.db.initialize()
                checks.append(
                    PreflightCheckResult(
                        "database", PreflightCheckStatus.PASS, "SQLite initialized"
                    )
                )
            except Exception as exc:
                msg = f"database connection/init failed: {exc}"
                checks.append(
                    PreflightCheckResult(
                        "database",
                        PreflightCheckStatus.FAIL,
                        msg,
                        reason_code=PreflightReasonCode.DATABASE_INACCESSIBLE.value,
                    )
                )
                failures.append(msg)
                reason_codes.append(PreflightReasonCode.DATABASE_INACCESSIBLE.value)
        else:
            checks.append(
                PreflightCheckResult(
                    "database", PreflightCheckStatus.PASS, "database not configured"
                )
            )

        # 5. Stream resolution (if station/url provided)
        if station and (page or url):
            try:
                resolved = self.resolver.resolve(station, page=page, url=url)
                if resolved.verification_status == "verified":
                    checks.append(
                        PreflightCheckResult(
                            "stream_resolution",
                            PreflightCheckStatus.PASS,
                            f"verified: {resolved.resolved_stream_url}",
                        )
                    )
                else:
                    err = resolved.details.get("error", "stream verification failed")
                    msg = f"stream resolution failed: {err}"
                    checks.append(
                        PreflightCheckResult(
                            "stream_resolution",
                            PreflightCheckStatus.FAIL,
                            msg,
                            reason_code=PreflightReasonCode.STREAM_UNRESOLVABLE.value,
                        )
                    )
                    failures.append(msg)
                    reason_codes.append(PreflightReasonCode.STREAM_UNRESOLVABLE.value)
            except Exception as exc:
                msg = f"stream resolver error: {exc}"
                checks.append(
                    PreflightCheckResult(
                        "stream_resolution",
                        PreflightCheckStatus.FAIL,
                        msg,
                        reason_code=PreflightReasonCode.STREAM_UNRESOLVABLE.value,
                    )
                )
                failures.append(msg)
                reason_codes.append(PreflightReasonCode.STREAM_UNRESOLVABLE.value)

        # Analysis Dependency Checks (only required in CAPTURE_AND_ANALYSIS mode)
        if mode == PreflightMode.CAPTURE_AND_ANALYSIS and analyzer == "whisper":
            transcriber = None
            engine_installed = False

            # 6. Transcription Engine Import
            try:
                from faster_whisper import WhisperModel  # noqa: F401

                engine_installed = True
                checks.append(
                    PreflightCheckResult(
                        "transcription_engine",
                        PreflightCheckStatus.PASS,
                        "faster-whisper available",
                    )
                )
            except ImportError as exc:
                msg = f"faster-whisper is not installed: {exc}"
                checks.append(
                    PreflightCheckResult(
                        "transcription_engine",
                        PreflightCheckStatus.FAIL,
                        msg,
                        reason_code=PreflightReasonCode.TRANSCRIPTION_ENGINE_MISSING.value,
                    )
                )
                failures.append(msg)
                reason_codes.append(PreflightReasonCode.TRANSCRIPTION_ENGINE_MISSING.value)

            # 7 & 8 & 9. Model loading, Device, Compute Type
            if engine_installed:
                try:
                    from ..transcription import SpeechTranscriber, TranscriptionConfig

                    cfg = TranscriptionConfig(model_size_or_path=model or "tiny")
                    transcriber = SpeechTranscriber(cfg)
                    _ = transcriber._load_model()
                    device = transcriber.device
                    compute_type = transcriber.compute_type

                    checks.append(
                        PreflightCheckResult(
                            "model_loading",
                            PreflightCheckStatus.PASS,
                            f"model '{cfg.model_size_or_path}' loaded",
                            metrics={"device": device, "compute_type": compute_type},
                        )
                    )
                    checks.append(
                        PreflightCheckResult(
                            "device", PreflightCheckStatus.PASS, f"device: {device}"
                        )
                    )
                    checks.append(
                        PreflightCheckResult(
                            "compute_type", PreflightCheckStatus.PASS, f"compute: {compute_type}"
                        )
                    )
                except Exception as exc:
                    msg = f"Whisper model loading failed: {exc}"
                    checks.append(
                        PreflightCheckResult(
                            "model_loading",
                            PreflightCheckStatus.FAIL,
                            msg,
                            reason_code=PreflightReasonCode.MODEL_LOAD_FAILED.value,
                        )
                    )
                    failures.append(msg)
                    reason_codes.append(PreflightReasonCode.MODEL_LOAD_FAILED.value)
            else:
                checks.append(
                    PreflightCheckResult(
                        "model_loading",
                        PreflightCheckStatus.SKIPPED,
                        "skipped: transcription engine missing",
                    )
                )

            # 10. Short Real Audio Sample Transcription Test
            if transcriber is not None and not any("model" in f.lower() for f in failures):
                try:
                    wav_bytes = generate_short_test_wav(1.0)
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                        tf.write(wav_bytes)
                        tf_path = Path(tf.name)
                    try:
                        res, err = transcriber.transcribe_safe(tf_path)
                        if err:
                            msg = f"Sample transcription failed: {err}"
                            checks.append(
                                PreflightCheckResult(
                                    "short_sample_test",
                                    PreflightCheckStatus.FAIL,
                                    msg,
                                    reason_code=PreflightReasonCode.SAMPLE_TRANSCRIPTION_FAILED.value,
                                )
                            )
                            failures.append(msg)
                            reason_codes.append(
                                PreflightReasonCode.SAMPLE_TRANSCRIPTION_FAILED.value
                            )
                        else:
                            checks.append(
                                PreflightCheckResult(
                                    "short_sample_test",
                                    PreflightCheckStatus.PASS,
                                    f"sample processed in {res.processing_time:.2f}s",
                                )
                            )
                    finally:
                        if tf_path.exists():
                            os.remove(tf_path)
                except Exception as exc:
                    msg = f"Sample transcription test failed: {exc}"
                    checks.append(
                        PreflightCheckResult(
                            "short_sample_test",
                            PreflightCheckStatus.FAIL,
                            msg,
                            reason_code=PreflightReasonCode.SAMPLE_TRANSCRIPTION_FAILED.value,
                        )
                    )
                    failures.append(msg)
                    reason_codes.append(PreflightReasonCode.SAMPLE_TRANSCRIPTION_FAILED.value)
        else:
            checks.append(
                PreflightCheckResult(
                    "transcription_engine",
                    PreflightCheckStatus.SKIPPED,
                    "transcription not required in CAPTURE_ONLY / baseline mode",
                )
            )

        passed = len(failures) == 0
        blocked = not passed and mode == PreflightMode.CAPTURE_AND_ANALYSIS

        return PreflightReport(
            mode=mode,
            passed=passed,
            blocked=blocked,
            checks=checks,
            failures=failures,
            reason_codes=reason_codes,
        )


def verify_preflight_or_raise(
    checker: PreflightChecker,
    mode: PreflightMode = PreflightMode.CAPTURE_AND_ANALYSIS,
    **kwargs: Any,
) -> PreflightReport:
    """Run preflight and raise PreflightError if required analysis dependencies fail."""
    report = checker.run_preflight(mode=mode, **kwargs)
    if report.blocked:
        failure_summary = "; ".join(report.failures)
        raise PreflightError(
            f"Preflight verification failed for {mode.value}. Start blocked: {failure_summary}"
        )
    return report
