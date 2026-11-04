"""Alert history backends for Icinga Web's IcingaDB and legacy monitoring modules."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

from icinga_mcp import formatting as fmt
from icinga_mcp.clients.icingaweb import IcingaWebClient, any_of, compare, eq
from icinga_mcp.models import HistoryEvent

# Category -> (raw event type -> readable event name). Both IDO type spellings of
# notifications occur depending on the Icinga Web version.
CATEGORIES: dict[str, dict[str, str]] = {
    "state": {"hard_state": "state change"},
    "soft_state": {"soft_state": "soft state change"},
    "notification": {"notification_state": "notification", "notify": "notification"},
    "acknowledgement": {"ack": "acknowledged", "ack_deleted": "acknowledgement removed"},
    "comment": {"comment": "comment added", "comment_deleted": "comment removed"},
    "downtime": {
        "dt_start": "downtime started",
        "dt_end": "downtime ended",
        "dt_comment": "downtime scheduled",
        "dt_comment_deleted": "downtime removed",
    },
    "flapping": {"flapping": "flapping started", "flapping_deleted": "flapping ended"},
}
_CATEGORY_OF = {raw: cat for cat, raws in CATEGORIES.items() for raw in raws}
_EVENT_NAME = {raw: name for raws in CATEGORIES.values() for raw, name in raws.items()}
_ACTOR_PREFIX = re.compile(r"^\[([^\]]*)\]\s?(.*)$", re.DOTALL)
_ATTEMPT_PREFIX = re.compile(r"^\[\s*(\d+/\d+)\s*\]\s?(.*)$", re.DOTALL)


class HistoryBackend(Protocol):
    async def events(
        self,
        *,
        start: float,
        end: float,
        host: str | None,
        service: str | None,
        categories: Sequence[str],
    ) -> list[HistoryEvent]: ...


class MonitoringHistory:
    """History from the Icinga Web 2 ``monitoring`` module (``/monitoring/list/eventhistory``)."""

    def __init__(self, web: IcingaWebClient) -> None:
        self._web = web

    async def events(
        self,
        *,
        start: float,
        end: float,
        host: str | None,
        service: str | None,
        categories: Sequence[str],
    ) -> list[HistoryEvent]:
        # The time window is mandatory: without it Icinga Web scans the whole history.
        filters = [compare("timestamp", ">=", start), compare("timestamp", "<=", end)]
        if host:
            filters.append(eq("host", host))
        if service:
            filters.append(eq("service", service))
        raw_types = [raw for cat in categories for raw in CATEGORIES[cat]]
        filters.append(any_of("type", raw_types))
        rows = await self._web.list("/monitoring/list/eventhistory", filters)
        wanted = set(raw_types)
        events = [_event(row) for row in rows if row.get("type") in wanted]
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events


_ICINGADB_EVENT_TYPES: dict[str, tuple[str, str]] = {
    "state_change": ("state", "state change"),
    "notification": ("notification", "notification"),
    "ack_set": ("acknowledgement", "acknowledged"),
    "ack_clear": ("acknowledgement", "acknowledgement removed"),
    "comment_add": ("comment", "comment added"),
    "comment_remove": ("comment", "comment removed"),
    "downtime_start": ("downtime", "downtime started"),
    "downtime_end": ("downtime", "downtime ended"),
    "flapping_start": ("flapping", "flapping started"),
    "flapping_end": ("flapping", "flapping ended"),
}


class IcingaDbHistory:
    """History from the IcingaDB Web module's JSON export."""

    def __init__(self, web: IcingaWebClient) -> None:
        self._web = web

    async def events(
        self,
        *,
        start: float,
        end: float,
        host: str | None,
        service: str | None,
        categories: Sequence[str],
    ) -> list[HistoryEvent]:
        event_types = list(
            dict.fromkeys(
                event_type
                for event_type, (category, _) in _ICINGADB_EVENT_TYPES.items()
                if category in categories
            )
        )
        filters = [
            _time_filter("event_time", ">=", start),
            _time_filter("event_time", "<=", end),
            any_of("event_type", event_types),
        ]
        if host:
            filters.append(eq("host.name", host))
        if service:
            filters.append(eq("service.name", service))
        rows = await self._web.list("/icingadb/history", filters)
        events = [event for row in rows if (event := _icingadb_event(row, categories)) is not None]
        events.sort(key=lambda event: event.timestamp, reverse=True)
        return events


def build_history_backend(web: IcingaWebClient, kind: str) -> HistoryBackend:
    if kind == "icingadb":
        return IcingaDbHistory(web)
    return MonitoringHistory(web)


def _time_filter(column: str, operator: str, timestamp: float) -> str:
    value = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M:%S")
    return f"{column}{operator}{quote(value, safe='')}"


def _icingadb_event(row: dict[str, Any], categories: Sequence[str]) -> HistoryEvent | None:
    raw_type = row.get("event_type")
    if not isinstance(raw_type, str):
        return None
    mapped = _ICINGADB_EVENT_TYPES.get(raw_type)
    if mapped is None:
        return None
    category, name = mapped
    if category not in categories:
        return None

    detail_key = {
        "state_change": "state",
        "notification": "notification",
        "ack_set": "acknowledgement",
        "ack_clear": "acknowledgement",
        "comment_add": "comment",
        "comment_remove": "comment",
        "downtime_start": "downtime",
        "downtime_end": "downtime",
        "flapping_start": "flapping",
        "flapping_end": "flapping",
    }[raw_type]
    detail = row.get(detail_key)
    detail = detail if isinstance(detail, dict) else {}

    state_detail = detail if raw_type == "state_change" else {}
    state_type = state_detail.get("state_type")
    if raw_type == "state_change":
        if state_type == "soft":
            if "soft_state" not in categories:
                return None
            category, name = "soft_state", "soft state change"
            state_value = state_detail.get("soft_state")
        else:
            state_value = state_detail.get("hard_state")
    else:
        state_value = None

    object_type = row.get("object_type")
    host = row.get("host")
    service = row.get("service")
    host_name = host.get("name", "") if isinstance(host, dict) else ""
    service_name = service.get("name") if isinstance(service, dict) else None
    state = None
    if state_value is not None:
        state = (fmt.service_state if object_type == "service" else fmt.host_state)(
            int(state_value)
        )

    actor_key = {
        "ack_set": "author",
        "ack_clear": "cleared_by",
        "comment_add": "author",
        "comment_remove": "removed_by",
        "downtime_start": "author",
        "downtime_end": "cancelled_by",
    }.get(raw_type)
    actor = detail.get(actor_key) if actor_key else None
    if actor is None and raw_type in {"ack_clear", "comment_remove", "downtime_end"}:
        actor = detail.get("author")

    timestamp = datetime.fromisoformat(str(row["event_time"]).replace("Z", "+00:00")).timestamp()
    output = state_detail.get("output") if raw_type == "state_change" else detail.get("comment", "")
    event = HistoryEvent(
        time=fmt.timestamp(timestamp) or "",
        category=category,
        event=name,
        host=str(host_name),
        service=str(service_name) if service_name else None,
        state=state,
        attempt=(str(state_detail["check_attempt"]) if state_type == "soft" else None),
        actor=str(actor) if actor else None,
        output=fmt.truncate(str(output or ""), fmt.MAX_OUTPUT_BRIEF),
    )
    event._timestamp = timestamp
    return event


def _event(row: dict[str, Any]) -> HistoryEvent:
    raw_type = str(row.get("type"))
    category = _CATEGORY_OF[raw_type]
    is_service = row.get("object_type") == "service"
    output = str(row.get("output") or "")
    actor = attempt = None
    if category in {"notification", "acknowledgement", "comment", "downtime"}:
        match = _ACTOR_PREFIX.match(output)
        if match:
            actor, output = match.group(1) or None, match.group(2)
    elif category == "soft_state":
        match = _ATTEMPT_PREFIX.match(output)
        if match:
            attempt, output = match.group(1), match.group(2)
    state_value = int(row.get("state") or -1)
    state = None
    if state_value >= 0:
        state = (fmt.service_state if is_service else fmt.host_state)(state_value)
    timestamp = float(row.get("timestamp") or 0)
    event = HistoryEvent(
        time=fmt.timestamp(timestamp) or "",
        category=category,
        event=_EVENT_NAME[raw_type],
        host=str(row.get("host_name") or ""),
        service=row.get("service_description") or None,
        state=state,
        attempt=attempt,
        actor=actor,
        output=fmt.truncate(output, fmt.MAX_OUTPUT_BRIEF),
    )
    event._timestamp = timestamp
    return event
