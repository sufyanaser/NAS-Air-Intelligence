"""Tests verifying zero-console Windows subprocess hardening."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from nas_air_intelligence.ffmpeg import probe_duration
from nas_air_intelligence.process import (
    CREATE_NO_WINDOW,
    popen_silent,
    run_silent,
    silent_creation_flags,
)
from nas_air_intelligence.recorder import reap_orphan_ffmpeg


def test_create_no_window_flag_value() -> None:
    if sys.platform == "win32":
        assert CREATE_NO_WINDOW == 0x0800_0000
        assert silent_creation_flags() == 0x0800_0000
        assert silent_creation_flags(0x0000_0200) == 0x0800_0200
    else:
        assert CREATE_NO_WINDOW == 0
        assert silent_creation_flags() == 0


def test_run_silent_injects_flag_on_windows() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="test")
        with patch("sys.platform", "win32"):
            run_silent(["dummy", "arg"])
            _, kwargs = mock_run.call_args
            assert "creationflags" in kwargs
            assert kwargs["creationflags"] & 0x0800_0000 == 0x0800_0000


def test_popen_silent_injects_flag_on_windows() -> None:
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        with patch("sys.platform", "win32"):
            popen_silent(["dummy", "arg"])
            _, kwargs = mock_popen.call_args
            assert "creationflags" in kwargs
            assert kwargs["creationflags"] & 0x0800_0000 == 0x0800_0000


def test_probe_duration_uses_silent_runner(tmp_path) -> None:
    audio_file = tmp_path / "dummy.mp3"
    audio_file.write_bytes(b"dummy")

    with (
        patch("nas_air_intelligence.ffmpeg.find_binary", return_value="ffprobe"),
        patch("nas_air_intelligence.ffmpeg.run_silent") as mock_silent,
    ):
        mock_silent.return_value = MagicMock(stdout='{"format": {"duration": "12.34"}}')
        dur = probe_duration(audio_file)
        assert dur == 12.34
        assert mock_silent.called
        if sys.platform == "win32":
            _, kwargs = mock_silent.call_args
            # run_silent adds CREATE_NO_WINDOW when it runs subprocess.run


def test_reap_orphan_ffmpeg_silent(tmp_path) -> None:
    pid_file = tmp_path / "ffmpeg.pid"
    pid_file.write_text("99999", encoding="utf-8")

    with (
        patch("nas_air_intelligence.recorder._is_ffmpeg_process", return_value=True),
        patch("nas_air_intelligence.recorder.run_silent") as mock_silent,
        patch("os.name", "nt"),
    ):
        reap_orphan_ffmpeg(tmp_path)
        assert mock_silent.called
        args, _ = mock_silent.call_args
        assert "taskkill" in args[0]
