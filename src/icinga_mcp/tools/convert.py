"""Conversions from raw Icinga API objects to the tool output models."""

from typing import Any

from icinga_mcp import formatting as fmt
from icinga_mcp.models import Problem, ServiceBrief
from icinga_mcp.redaction import redact

_STATE_ATTRS = [
    "name",
    "display_name",
    "state",
    "state_type",
    "handled",
    "acknowledgement",
    "downtime_depth",
    "flapping",
    "last_state_change",
    "last_check",
    "last_check_result",
    "severity",
]

_DETAIL_ATTRS = [
    "last_hard_state_change",
    "next_check",
    "check_attempt",
    "max_check_attempts",
    "check_command",
    "check_interval",
    "groups",
    "templates",
    "zone",
    "notes",
    "notes_url",
    "action_url",
    "enable_notifications",
    "enable_active_checks",
    "vars",
]

# Icinga rejects attributes that do not exist on the object type (e.g. host_name on hosts).
HOST_PROBLEM_ATTRS = _STATE_ATTRS
SERVICE_PROBLEM_ATTRS = [*_STATE_ATTRS, "host_name"]
HOST_DETAIL_ATTRS = [*_STATE_ATTRS, *_DETAIL_ATTRS, "address", "address6"]
SERVICE_DETAIL_ATTRS = [*SERVICE_PROBLEM_ATTRS, *_DETAIL_ATTRS]


def problem_from(obj: dict[str, Any], kind: str) -> Problem:
    attrs = obj.get("attrs", {})
    is_host = kind == "host"
    return Problem(
        type="host" if is_host else "service",
        host=attrs.get("name") if is_host else attrs.get("host_name", ""),
        service=None if is_host else attrs.get("name"),
        display_name=attrs.get("display_name") or attrs.get("name", ""),
        state=(fmt.host_state if is_host else fmt.service_state)(attrs.get("state")),
        state_type=fmt.state_type(attrs.get("state_type")),
        handled=bool(attrs.get("handled")),
        acknowledged=bool(attrs.get("acknowledgement")),
        in_downtime=bool(attrs.get("downtime_depth")),
        flapping=bool(attrs.get("flapping")),
        since=fmt.timestamp(attrs.get("last_state_change")),
        duration=fmt.duration_since(attrs.get("last_state_change")),
        last_check=fmt.timestamp(attrs.get("last_check")),
        output=fmt.check_output(attrs, fmt.MAX_OUTPUT_BRIEF),
    )


def problem_sort_key(obj: dict[str, Any]) -> tuple[bool, float, float]:
    attrs = obj.get("attrs", {})
    return (
        bool(attrs.get("handled")),
        -float(attrs.get("severity") or 0),
        -float(attrs.get("last_state_change") or 0),
    )


def service_brief(obj: dict[str, Any]) -> ServiceBrief:
    attrs = obj.get("attrs", {})
    return ServiceBrief(
        name=attrs.get("name", ""),
        display_name=attrs.get("display_name") or attrs.get("name", ""),
        state=fmt.service_state(attrs.get("state")),
        state_type=fmt.state_type(attrs.get("state_type")),
        handled=bool(attrs.get("handled")),
        since=fmt.timestamp(attrs.get("last_state_change")),
        output=fmt.check_output(attrs, fmt.MAX_OUTPUT_BRIEF),
    )


def details_fields(attrs: dict[str, Any], *, is_host: bool) -> dict[str, Any]:
    """Common fields of ``ObjectDetails`` for hosts and services."""
    interval = attrs.get("check_interval")
    attempt = f"{_int(attrs.get('check_attempt'))}/{_int(attrs.get('max_check_attempts'))}"
    fields: dict[str, Any] = {
        "name": attrs.get("name", ""),
        "display_name": attrs.get("display_name") or attrs.get("name", ""),
        "state": (fmt.host_state if is_host else fmt.service_state)(attrs.get("state")),
        "state_type": fmt.state_type(attrs.get("state_type")),
        "handled": bool(attrs.get("handled")),
        "acknowledged": bool(attrs.get("acknowledgement")),
        "in_downtime": bool(attrs.get("downtime_depth")),
        "flapping": bool(attrs.get("flapping")),
        "since": fmt.timestamp(attrs.get("last_state_change")),
        "duration": fmt.duration_since(attrs.get("last_state_change")),
        "last_hard_state_change": fmt.timestamp(attrs.get("last_hard_state_change")),
        "last_check": fmt.timestamp(attrs.get("last_check")),
        "next_check": fmt.timestamp(attrs.get("next_check")),
        "check_attempt": attempt,
        "check_command": attrs.get("check_command"),
        "check_interval_seconds": float(interval) if interval is not None else None,
        "output": fmt.check_output(attrs),
        "performance_data": fmt.performance_data(attrs),
        "groups": list(attrs.get("groups") or []),
        "templates": list(attrs.get("templates") or []),
        "zone": attrs.get("zone") or None,
        "notes": attrs.get("notes") or None,
        "notes_url": attrs.get("notes_url") or None,
        "action_url": attrs.get("action_url") or None,
        "enable_notifications": attrs.get("enable_notifications"),
        "enable_active_checks": attrs.get("enable_active_checks"),
        "vars": redact(attrs.get("vars") or {}),
    }
    return fields


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
