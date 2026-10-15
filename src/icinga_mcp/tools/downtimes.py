import time
from typing import Annotated, Any

from pydantic import Field

from icinga_mcp import formatting as fmt
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.models import Downtime, DowntimeList
from icinga_mcp.tools.base import Filter, ToolSpec, icinga_tool

TOOLSET = "downtimes"


def build(client: IcingaClient) -> list[ToolSpec]:
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

    return [ToolSpec(list_downtimes, "list_downtimes", TOOLSET, "List downtimes")]


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
