from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Literal

HOST_STATES = {0: "UP", 1: "DOWN", 2: "UNREACHABLE"}
SERVICE_STATES = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}
COMMENT_TYPES = {1: "user", 2: "downtime", 3: "flapping", 4: "acknowledgement"}

MAX_OUTPUT = 1000
MAX_OUTPUT_BRIEF = 300


def host_state(value: Any) -> str:
    return HOST_STATES.get(_int(value), "PENDING")


def service_state(value: Any) -> str:
    return SERVICE_STATES.get(_int(value), "PENDING")


def state_type(value: Any) -> Literal["hard", "soft"]:
    return "soft" if _int(value) == 0 else "hard"


def comment_type(value: Any) -> str:
    return COMMENT_TYPES.get(_int(value), "unknown")


def timestamp(value: Any) -> str | None:
    """Icinga epoch seconds -> ISO 8601 in local time with offset; 0/empty -> None."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def duration_since(value: Any, now: float | None = None) -> str | None:
    if not value:
        return None
    seconds = int((now if now is not None else time.time()) - float(value))
    return human_duration(max(seconds, 0))


def human_duration(seconds: int) -> str:
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def truncate(value: Any, limit: int = MAX_OUTPUT) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def check_output(attrs: dict[str, Any], limit: int = MAX_OUTPUT) -> str:
    result = attrs.get("last_check_result") or {}
    return truncate(result.get("output", ""), limit)


def performance_data(attrs: dict[str, Any]) -> list[str]:
    result = attrs.get("last_check_result") or {}
    perf = result.get("performance_data") or []
    return [str(item) for item in perf][:50]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1
