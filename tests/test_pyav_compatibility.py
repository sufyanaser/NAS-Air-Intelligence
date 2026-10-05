import subprocess
import tempfile
from pathlib import Path

import av
from packaging.version import Version


def test_pyav_version_is_below_19():
    """Ensure PyAV version remains below 19.0.0 to prevent metadata_errors breakage."""
    parsed = Version(av.__version__)
    msg = f"PyAV {av.__version__} is >= 19.0.0 and breaks faster-whisper"
    assert parsed < Version("19.0.0"), msg


def test_faster_whisper_decode_audio_regression():
    """Regression test ensuring decode_audio does not fail with metadata_errors."""
    from faster_whisper.audio import decode_audio

    # Generate a brief 1-second silence WAV with ffmpeg
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
                "anullsrc=r=16000:cl=mono",
                "-t",
                "1.0",
                "-y",
                str(wav_path),
            ],
            check=True,
        )

        audio = decode_audio(str(wav_path))
        assert audio is not None
        assert len(audio) == 16000
        assert audio.dtype.name == "float32"
    finally:
        if wav_path.exists():
            wav_path.unlink()
