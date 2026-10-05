import pytest

from nas_air_intelligence.util import parse_duration


def test_parse_duration_units():
    assert parse_duration("90s") == 90
    assert parse_duration("15m") == 900
    assert parse_duration("24h") == 86400
    assert parse_duration("2") == 2


def test_parse_duration_rejects_bad_value():
    with pytest.raises(ValueError):
        parse_duration("tomorrow")
