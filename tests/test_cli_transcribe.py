import json
from unittest.mock import patch

from nas_air_intelligence.cli import main
from nas_air_intelligence.transcription import (
    TranscriptionResult,
    TranscriptionSegment,
)


def test_cli_transcribe_success(tmp_path, capsys):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"content")

    mock_result = TranscriptionResult(
        text="مرحبا بكم في البث",
        language="ar",
        language_probability=0.99,
        duration=3.0,
        processing_time=0.15,
        segments=[
            TranscriptionSegment(
                start=0.0,
                end=3.0,
                text="مرحبا بكم في البث",
                avg_logprob=-0.1,
                no_speech_prob=0.01,
                confidence=0.9,
            )
        ],
        model="tiny",
        device="cuda",
        compute_type="float16",
    )

    with patch(
        "nas_air_intelligence.transcription.SpeechTranscriber.transcribe",
        return_value=mock_result,
    ):
        exit_code = main(["transcribe", str(audio_file), "--language", "ar"])
        assert exit_code == 0
        captured = capsys.readouterr().out
        assert "مرحبا بكم في البث" in captured
        assert "Device: cuda" in captured
        assert "Model: tiny" in captured


def test_cli_transcribe_json(tmp_path, capsys):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"content")

    mock_result = TranscriptionResult(
        text="تقرير الأخبار",
        language="ar",
        language_probability=0.95,
        duration=2.0,
        processing_time=0.1,
        segments=[],
        model="tiny",
        device="cpu",
        compute_type="int8",
    )

    with patch(
        "nas_air_intelligence.transcription.SpeechTranscriber.transcribe",
        return_value=mock_result,
    ):
        exit_code = main(["transcribe", str(audio_file), "--json"])
        assert exit_code == 0
        captured = capsys.readouterr().out
        data = json.loads(captured)
        assert data["text"] == "تقرير الأخبار"
        assert data["language"] == "ar"
        assert data["device"] == "cpu"


def test_cli_doctor_outputs_cuda_status(capsys):
    exit_code = main(["doctor"])
    assert exit_code in (0, 2)
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert "cuda_available" in data
    assert "ffmpeg" in data
    assert "ffprobe" in data
