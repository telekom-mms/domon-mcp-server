"""Parsing of user-supplied times and durations (for downtimes and acknowledgement expiry)."""

from __future__ import annotations

import re
import time
from datetime import datetime

_DURATION = re.compile(r"^(?:(\d+)d)?\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?$")


def parse_duration(text: str) -> int:
    """Parse durations like ``90m``, ``2h``, ``1d 4h`` or ``1h30m`` into seconds."""
    value = text.strip().lower()
    match = _DURATION.fullmatch(value)
    if not value or not match or not any(match.groups()):
        raise ValueError(f"invalid duration {text!r}; use e.g. '30m', '2h', '1d', '1h30m'")
    days, hours, minutes, seconds = (int(group or 0) for group in match.groups())
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        raise ValueError(f"duration must be positive: {text!r}")
    return total


def parse_time(text: str, now: float | None = None) -> float:
    """Parse ``now`` or an ISO 8601 timestamp into epoch seconds.

    Timestamps without a UTC offset are interpreted in the local time zone of the server.
    """
    value = text.strip()
    if value.lower() == "now":
        return now if now is not None else time.time()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"invalid time {text!r}; use 'now' or ISO 8601 like '2026-10-16T08:00' "
            "or '2026-10-16T08:00+02:00'"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()
