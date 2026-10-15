import time
from typing import Annotated, Any

from mcp.server.mcpserver import Context, Elicit, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from icinga_mcp import formatting as fmt
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import ServerSettings
from icinga_mcp.models import ActionResult, Downtime, DowntimeList
from icinga_mcp.tools.actions import (
    CONFIRM_DESCRIPTION,
    TARGETS_DESCRIPTION,
    Approval,
    ResolvedTargets,
    approval,
    check_author,
    duration,
    finish,
    normalize_names,
    not_applied,
    point_in_time,
    resolve_targets,
    run_action,
    run_object_action,
    target_lines,
)
from icinga_mcp.tools.base import Filter, ToolSpec, icinga_tool

TOOLSET = "downtimes"


def build(client: IcingaClient, settings: ServerSettings) -> list[ToolSpec]:
    @icinga_tool
    async def list_downtimes(
        host: Annotated[str | None, Field(description="Host name or glob pattern")] = None,
        service: Annotated[
            str | None,
            Field(description="Service name or glob pattern; omit for host and service downtimes"),
        ] = None,
        in_effect_only: Annotated[
            bool, Field(description="Only downtimes that are currently active")
        ] = False,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> DowntimeList:
        """List scheduled downtimes for hosts and services (planned maintenance windows),
        sorted by start time. Shows whether each downtime is currently in effect."""
        flt = Filter()
        if host:
            flt.add("match(host_pattern, downtime.host_name)", host_pattern=host)
        if service:
            flt.add("match(service_pattern, downtime.service_name)", service_pattern=service)
        downtimes = await client.query_objects(
            "downtimes",
            filter=flt.expression,
            filter_vars=flt.vars,
            attrs=[
                "__name",
                "host_name",
                "service_name",
                "author",
                "comment",
                "start_time",
                "end_time",
                "fixed",
                "duration",
                "trigger_time",
                "triggered_by",
            ],
        )
        downtimes.sort(key=lambda d: float(d.get("attrs", {}).get("start_time") or 0))
        now = time.time()
        items = [_downtime(d.get("attrs", {}), now) for d in downtimes]
        if in_effect_only:
            items = [d for d in items if d.in_effect]
        return DowntimeList(
            total=len(items),
            returned=min(len(items), limit),
            truncated=len(items) > limit,
            downtimes=items[:limit],
        )

    @icinga_tool
    async def resolve(targets: list[str]) -> ResolvedTargets:
        return await resolve_targets(client, settings, targets)

    def schedule_message(
        resolved: ResolvedTargets,
        comment: str,
        start: str,
        end: str | None,
        duration: str | None,
        fixed: bool,
        flexible_duration: str | None,
        all_services: bool,
    ) -> str:
        kind = "fixed" if fixed else f"flexible (lasts {flexible_duration} once a problem occurs)"
        until = end if end else f"start + {duration}"
        lines = [
            f"Schedule a {kind} downtime for {len(resolved.targets)} object(s) in Icinga as "
            f"'{settings.author}':",
            target_lines(resolved.targets),
            f"Window: {start} until {until}",
            f"Comment: {comment}",
        ]
        if all_services:
            lines.append("Hosts: including all their services")
        return "\n".join(lines)

    @icinga_tool
    async def confirm_schedule(
        resolved: Annotated[ResolvedTargets, Resolve(resolve)],
        comment: str,
        ctx: Context,
        start: str = "now",
        end: str | None = None,
        duration: str | None = None,
        fixed: bool = True,
        flexible_duration: str | None = None,
        all_services: bool = False,
        confirm: bool = False,
    ) -> Approval | Elicit[Approval]:
        _plan(settings, start, end, duration, fixed, flexible_duration, time.time())
        message = schedule_message(
            resolved, comment, start, end, duration, fixed, flexible_duration, all_services
        )
        return approval(ctx, settings, message, confirm)

    @icinga_tool
    async def schedule_downtime(
        targets: Annotated[list[str], Field(min_length=1, description=TARGETS_DESCRIPTION)],
        comment: Annotated[
            str, Field(min_length=1, description="Reason, e.g. maintenance and ticket number")
        ],
        resolved: Annotated[ResolvedTargets, Resolve(resolve)],
        approved: Annotated[Approval, Resolve(confirm_schedule)],
        start: Annotated[
            str,
            Field(
                description="'now' or ISO 8601 time, e.g. '2026-10-16T22:00' (local time of "
                "the MCP server unless an offset like '+02:00' is given)"
            ),
        ] = "now",
        end: Annotated[
            str | None, Field(description="End as ISO 8601 time; alternatively use duration")
        ] = None,
        duration: Annotated[
            str | None, Field(description="Length from start, e.g. '2h', '90m', '1d'")
        ] = None,
        fixed: Annotated[
            bool,
            Field(
                description="True: active for the whole window. False (flexible): starts "
                "when a problem occurs within the window and lasts flexible_duration"
            ),
        ] = True,
        flexible_duration: Annotated[
            str | None, Field(description="Required for flexible downtimes, e.g. '1h'")
        ] = None,
        all_services: Annotated[
            bool, Field(description="For host targets: also put all services of the host down")
        ] = False,
        confirm: Annotated[bool, Field(description=CONFIRM_DESCRIPTION)] = False,
    ) -> ActionResult:
        """Schedule a downtime (planned maintenance) for hosts or services so that problems in
        this window count as handled and do not notify. Returns the names of the created
        downtimes (needed for remove_downtime). Changes Icinga: the user must confirm before
        anything is applied."""
        author = check_author(settings)
        start_ts, end_ts, flex = _plan(
            settings, start, end, duration, fixed, flexible_duration, time.time()
        )
        names = [t.name for t in resolved.targets]
        message = schedule_message(
            resolved, comment, start, end, duration, fixed, flexible_duration, all_services
        )
        pending = not_applied("schedule_downtime", message, approved, names, [])
        if pending:
            return pending
        params: dict[str, Any] = {
            "author": author,
            "comment": comment,
            "start_time": start_ts,
            "end_time": end_ts,
            "fixed": fixed,
            "duration": flex if not fixed else end_ts - start_ts,
        }
        hosts = [t for t in resolved.targets if not t.is_service]
        services = [t for t in resolved.targets if t.is_service]
        outcomes = await run_action(
            client,
            "schedule-downtime",
            hosts,
            {**params, "all_services": True} if all_services else params,
        )
        outcomes += await run_action(client, "schedule-downtime", services, params)
        return finish("schedule_downtime", names, [], outcomes)

    @icinga_tool
    async def resolve_downtimes(names: list[str]) -> ResolvedDowntimes:
        wanted = normalize_names(names, settings, "downtime names")
        found = await client.query_objects(
            "downtimes",
            filter="downtime.__name in names",
            filter_vars={"names": wanted},
            attrs=["__name", "host_name", "service_name", "author", "comment"],
        )
        by_name = {
            d.get("attrs", {}).get("__name") or d.get("name"): d.get("attrs", {}) for d in found
        }
        missing = [n for n in wanted if n not in by_name]
        if missing:
            raise ToolError(
                f"Unknown downtime(s): {', '.join(missing)}. Nothing was changed. "
                "Use list_downtimes to get the names."
            )
        return ResolvedDowntimes(
            names=wanted,
            lines=[_downtime_line(n, by_name[n]) for n in wanted],
        )

    def remove_message(resolved: "ResolvedDowntimes") -> str:
        return f"Remove {len(resolved.names)} downtime(s):\n" + "\n".join(resolved.lines)

    @icinga_tool
    async def confirm_remove(
        resolved: Annotated[ResolvedDowntimes, Resolve(resolve_downtimes)],
        ctx: Context,
        confirm: bool = False,
    ) -> Approval | Elicit[Approval]:
        return approval(ctx, settings, remove_message(resolved), confirm)

    @icinga_tool
    async def remove_downtime(
        names: Annotated[
            list[str],
            Field(min_length=1, description="Downtime names as returned by list_downtimes"),
        ],
        resolved: Annotated[ResolvedDowntimes, Resolve(resolve_downtimes)],
        approved: Annotated[Approval, Resolve(confirm_remove)],
        confirm: Annotated[bool, Field(description=CONFIRM_DESCRIPTION)] = False,
    ) -> ActionResult:
        """Remove (cancel) scheduled downtimes by name, e.g. when maintenance finished early.
        Changes Icinga: the user must confirm before anything is applied."""
        check_author(settings)
        pending = not_applied(
            "remove_downtime", remove_message(resolved), approved, resolved.names, []
        )
        if pending:
            return pending
        outcomes = await run_object_action(
            client,
            "remove-downtime",
            {
                "type": "Downtime",
                "filter": "downtime.__name in names",
                "filter_vars": {"names": resolved.names},
            },
        )
        return finish("remove_downtime", resolved.names, [], outcomes)

    return [
        ToolSpec(list_downtimes, "list_downtimes", TOOLSET, "List downtimes"),
        ToolSpec(
            schedule_downtime,
            "schedule_downtime",
            TOOLSET,
            "Schedule a downtime",
            write=True,
            idempotent=False,
        ),
        ToolSpec(remove_downtime, "remove_downtime", TOOLSET, "Remove downtimes", write=True),
    ]


class ResolvedDowntimes(BaseModel):
    names: list[str]
    lines: list[str]


def _downtime_line(name: str, attrs: dict[str, Any]) -> str:
    target = attrs.get("host_name", "")
    if attrs.get("service_name"):
        target += f"!{attrs['service_name']}"
    return f"- {name} ({target}, by {attrs.get('author', '?')}: {attrs.get('comment', '')})"


def _plan(
    settings: ServerSettings,
    start: str,
    end: str | None,
    length: str | None,
    fixed: bool,
    flexible_duration: str | None,
    now: float,
) -> tuple[float, float, int]:
    """Validate the downtime window; returns start, end and the flexible duration (seconds)."""
    if (end is None) == (length is None):
        raise ToolError("Give exactly one of 'end' or 'duration'.")
    start_ts = point_in_time(start, "start", now)
    end_ts = point_in_time(end, "end", now) if end else start_ts + duration(str(length), "duration")
    if end_ts <= start_ts:
        raise ToolError("The downtime must end after it starts.")
    if end_ts <= now:
        raise ToolError("The downtime window is already over.")
    if end_ts - start_ts > settings.max_downtime_seconds:
        raise ToolError(
            f"The downtime is longer than the allowed maximum of {settings.max_downtime} "
            "(ICINGA_MCP_MAX_DOWNTIME)."
        )
    flex = 0
    if not fixed:
        if not flexible_duration:
            raise ToolError("Flexible downtimes need 'flexible_duration'.")
        flex = duration(flexible_duration, "flexible_duration")
        if flex > end_ts - start_ts:
            raise ToolError("'flexible_duration' must not be longer than the downtime window.")
    return start_ts, end_ts, flex


def _downtime(attrs: dict[str, Any], now: float) -> Downtime:
    fixed = bool(attrs.get("fixed"))
    start = float(attrs.get("start_time") or 0)
    end = float(attrs.get("end_time") or 0)
    trigger = float(attrs.get("trigger_time") or 0)
    duration = float(attrs.get("duration") or 0)
    # Flexible downtimes start when a problem occurs and then last `duration` seconds.
    in_effect = start <= now < end if fixed else trigger > 0 and now < trigger + duration
    return Downtime(
        name=attrs.get("__name", ""),
        host=attrs.get("host_name", ""),
        service=attrs.get("service_name") or None,
        author=attrs.get("author", ""),
        comment=fmt.truncate(attrs.get("comment", "")),
        start=fmt.timestamp(start),
        end=fmt.timestamp(end),
        fixed=fixed,
        duration=None if fixed else fmt.human_duration(int(duration)),
        in_effect=in_effect,
        triggered_by=attrs.get("triggered_by") or None,
    )
