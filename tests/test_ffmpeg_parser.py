from nas_air_intelligence.ffmpeg import parse_silencedetect


def test_parse_silencedetect_builds_audio_and_silence_intervals():
    stderr = """
[silencedetect @ 0x1] silence_start: 2.5
[silencedetect @ 0x1] silence_end: 4.0 | silence_duration: 1.5
[silencedetect @ 0x1] silence_start: 8.0
"""
    assert parse_silencedetect(stderr, 10.0) == [
        ("audio", 0.0, 2.5),
        ("silence", 2.5, 4.0),
        ("audio", 4.0, 8.0),
        ("silence", 8.0, 10.0),
    ]


def test_parse_silencedetect_defaults_to_audio():
    assert parse_silencedetect("", 5.0) == [("audio", 0.0, 5.0)]
