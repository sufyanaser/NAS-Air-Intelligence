import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from nas_air_intelligence.ffmpeg import acoustic_fingerprint


def test_acoustic_fingerprint_synthetic_audio():
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg binary not available")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = Path(f.name)

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:duration=3",
                "-y",
                str(wav_path),
            ],
            check=True,
        )

        fp = acoustic_fingerprint(wav_path)
        if fp is None:
            pytest.skip("FFmpeg lacks chromaprint muxer support in this environment")
        assert isinstance(fp, str)
        assert len(fp) > 10
    finally:
        if wav_path.exists():
            wav_path.unlink()


def test_acoustic_fingerprint_missing_tool():
    fp = acoustic_fingerprint("any.wav", ffmpeg="non_existent_ffmpeg_bin")
    assert fp is None


def test_acoustic_fingerprint_nonexistent_file():
    fp = acoustic_fingerprint("nonexistent_file_xyz.wav")
    assert fp is None
