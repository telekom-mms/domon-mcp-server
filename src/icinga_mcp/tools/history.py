import collections
import time
from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from icinga_mcp import formatting as fmt
from icinga_mcp.config import ServerSettings
from icinga_mcp.history import HistoryBackend
from icinga_mcp.models import HistoryEvent, HistoryResult, HistorySummary, ObjectActivity
from icinga_mcp.timeparse import parse_duration, parse_time
from icinga_mcp.tools.base import ToolSpec, icinga_tool

TOOLSET = "history"

Category = Literal[
    "state", "soft_state", "notification", "acknowledgement", "comment", "downtime", "flapping"
]
DEFAULT_CATEGORIES: list[Category] = ["state", "notification", "acknowledgement", "downtime"]
OK_STATES = {"OK", "UP"}
MOST_ACTIVE = 10


def build(history: HistoryBackend, settings: ServerSettings) -> list[ToolSpec]:
    @icinga_tool
    async def get_history(
        host: Annotated[
            str | None, Field(description="Host name or pattern with *, e.g. 'web*'")
        ] = None,
        service: Annotated[
            str | None,
            Field(description="Service name or pattern with * (the name, not the display name)"),
        ] = None,
        since: Annotated[
            str,
            Field(
                description="Start of the window: duration ago like '24h', '7d', '90m' or an "
                "ISO 8601 time"
            ),
        ] = "24h",
        until: Annotated[
            str | None, Field(description="End of the window as ISO 8601 time; default: now")
        ] = None,
        events: Annotated[
            list[Category] | None,
            Field(
                description="Event categories; default: state (hard state changes), "
                "notification, acknowledgement, downtime. 'soft_state' is noisy"
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=1000, description="Maximum events returned")] = 100,
    ) -> HistoryResult:
        """Alert history from Icinga: state changes, notifications, acknowledgements, comments,
        downtimes and flapping within a time window, newest first. The summary counts all
        matching events and lists the objects with the most state changes (useful for
        "what happened last night" or finding flapping checks). Narrow by host/service for
        long windows."""
        now = time.time()
        start = _point(since, now, "since", relative=True)
        end = _point(until, now, "until") if until else now
        if end <= start:
            raise ToolError("'until' must be after 'since'.")
        if end - start > settings.max_history_seconds:
            raise ToolError(
                f"The window is longer than the allowed {settings.max_history} "
                "(ICINGA_MCP_MAX_HISTORY); narrow it."
            )
        categories = list(dict.fromkeys(events or DEFAULT_CATEGORIES))
        found = await history.events(
            start=start, end=end, host=host, service=service, categories=categories
        )
        return HistoryResult(
            start=fmt.timestamp(start) or "",
            end=fmt.timestamp(end) or "",
            summary=_summary(found),
            returned=min(len(found), limit),
            truncated=len(found) > limit,
            events=found[:limit],
        )

    return [ToolSpec(get_history, "get_history", TOOLSET, "Alert history")]


def _point(text: str, now: float, what: str, *, relative: bool = False) -> float:
    if relative:
        try:
            return now - parse_duration(text)
        except ValueError:
            pass
    try:
        return parse_time(text, now)
    except ValueError as exc:
        hint = " or a duration like '24h'" if relative else ""
        raise ToolError(f"{what}: {exc}{hint}") from None


def _summary(events: list[HistoryEvent]) -> HistorySummary:
    changes = [e for e in events if e.category == "state"]
    per_object: dict[str, list[HistoryEvent]] = collections.defaultdict(list)
    for event in changes:
        key = f"{event.host}!{event.service}" if event.service else event.host
        per_object[key].append(event)
    activity = [
        ObjectActivity(
            object=name,
            state_changes=len(items),
            problem_changes=sum(e.state not in OK_STATES for e in items),
            last_state=items[0].state,
            last_change=items[0].time,
        )
        for name, items in per_object.items()
    ]
    activity.sort(key=lambda a: (-a.state_changes, a.object))
    return HistorySummary(
        total=len(events),
        by_event=dict(collections.Counter(e.event for e in events)),
        state_changes_by_state=dict(collections.Counter(e.state or "?" for e in changes)),
        most_active=activity[:MOST_ACTIVE],
    )
