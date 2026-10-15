from __future__ import annotations

from datetime import UTC, datetime

import pytest

from icinga_mcp.timeparse import parse_duration, parse_time


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30m", 1800), ("2h", 7200), ("1d", 86400), ("1h30m", 5400), ("1d 4h", 100800), ("45s", 45)],
)
def test_parse_duration(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "soon", "2", "-1h", "0m", "1w"])
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


def test_parse_time() -> None:
    assert parse_time("now", now=123.0) == 123.0
    assert (
        parse_time("2026-10-16T08:00:00+00:00") == datetime(2026, 10, 16, 8, tzinfo=UTC).timestamp()
    )
    assert parse_time("2026-10-16T08:00:00Z") == parse_time("2026-10-16T10:00:00+02:00")
    local = datetime(2026, 10, 16, 8).astimezone().timestamp()
    assert parse_time("2026-10-16T08:00") == local
    with pytest.raises(ValueError, match="ISO 8601"):
        parse_time("tomorrow")
