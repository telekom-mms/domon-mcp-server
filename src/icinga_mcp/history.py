"""Alert history backends. The Icinga 2 API has no history, so it comes from Icinga Web.

``MonitoringHistory`` reads the IDO database through the Icinga Web 2 ``monitoring`` module.
An IcingaDB backend (``/icingadb/history``) can implement the same protocol later.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

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
