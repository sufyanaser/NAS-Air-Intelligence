import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nas_air_intelligence.transcription import (
    SpeechTranscriber,
    TranscriptionConfig,
    TranscriptionResult,
    ensure_cuda_dll_path,
)


def test_ensure_cuda_dll_path_runs():
    added = ensure_cuda_dll_path()
    assert isinstance(added, list)


def test_transcription_config_defaults():
    cfg = TranscriptionConfig()
    assert cfg.model_size_or_path == "tiny"
    assert cfg.device == "auto"
    assert cfg.compute_type == "auto"
    assert cfg.language == "ar"
    assert cfg.beam_size == 5
    assert cfg.allow_cpu_fallback is True


def test_resolve_device_and_compute_auto():
    transcriber = SpeechTranscriber(TranscriptionConfig(device="auto", compute_type="auto"))
    mock_c2 = MagicMock()
    mock_c2.get_cuda_device_count.return_value = 1
    with patch.dict(sys.modules, {"ctranslate2": mock_c2}):
        device, compute = transcriber._resolve_device_and_compute()
        assert device == "cuda"
        assert compute == "float16"

    mock_c2.get_cuda_device_count.return_value = 0
    with patch.dict(sys.modules, {"ctranslate2": mock_c2}):
        device, compute = transcriber._resolve_device_and_compute()
        assert device == "cpu"
        assert compute == "int8"


def test_resolve_device_and_compute_explicit():
    transcriber = SpeechTranscriber(TranscriptionConfig(device="cpu", compute_type="float32"))
    device, compute = transcriber._resolve_device_and_compute()
    assert device == "cpu"
    assert compute == "float32"


def test_transcribe_mocked(tmp_path):
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"dummy wav content")

    mock_seg = SimpleNamespace(
        start=0.0,
        end=2.5,
        text="  السلام عليكم  ",
        avg_logprob=-0.15,
        no_speech_prob=0.01,
    )
    mock_info = SimpleNamespace(
        language="ar",
        language_probability=0.98,
        duration=2.5,
    )

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_seg], mock_info)

    transcriber = SpeechTranscriber(
        TranscriptionConfig(model_size_or_path="tiny", device="cpu", compute_type="int8")
    )
    transcriber._model = mock_model
    transcriber._resolved_device = "cpu"
    transcriber._resolved_compute_type = "int8"

    result = transcriber.transcribe(audio_file)
    assert isinstance(result, TranscriptionResult)
    assert result.text == "السلام عليكم"
    assert result.language == "ar"
    assert result.language_probability == 0.98
    assert len(result.segments) == 1
    assert result.segments[0].start == 0.0
    assert result.segments[0].end == 2.5
    assert result.segments[0].text == "السلام عليكم"
    assert result.segments[0].confidence is not None
    assert 0.0 <= result.segments[0].confidence <= 1.0


def test_transcribe_safe_returns_error_on_failure(tmp_path):
    audio_file = tmp_path / "broken.wav"
    audio_file.write_bytes(b"data")

    transcriber = SpeechTranscriber()
    with patch.object(transcriber, "transcribe", side_effect=RuntimeError("GPU OOM")):
        result, error = transcriber.transcribe_safe(audio_file)
        assert result is None
        assert "GPU OOM" in str(error)


def test_transcribe_rejects_missing_file():
    transcriber = SpeechTranscriber()
    with pytest.raises(FileNotFoundError):
        transcriber.transcribe("non_existent_file.wav")
