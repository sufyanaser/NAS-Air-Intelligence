import subprocess
import tempfile
from pathlib import Path

import pytest

from nas_air_intelligence.transcription import (
    SpeechTranscriber,
    TranscriptionConfig,
    ensure_cuda_dll_path,
)


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


@pytest.mark.skipif(not _cuda_available(), reason="CUDA device not available")
def test_gpu_whisper_transcription_runtime():
    """Verify end-to-end GPU transcription on CUDA device with float16."""
    ensure_cuda_dll_path()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = Path(f.name)

    try:
        # Generate 2-second synthetic audio
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=2",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-y",
                str(wav_path),
            ],
            check=True,
        )

        config = TranscriptionConfig(
            model_size_or_path="tiny",
            device="cuda",
            compute_type="float16",
        )
        transcriber = SpeechTranscriber(config)
        result = transcriber.transcribe(wav_path)

        assert result is not None
        assert result.device == "cuda"
        assert result.compute_type == "float16"
        assert result.duration > 0
        assert result.processing_time > 0
    finally:
        if wav_path.exists():
            wav_path.unlink()
