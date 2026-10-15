import collections
from typing import Annotated, Any, Literal

from pydantic import Field

from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import ServerSettings
from icinga_mcp.models import ProblemList, ProblemSummary
from icinga_mcp.tools.base import Filter, ToolSpec, icinga_tool
from icinga_mcp.tools.convert import (
    HOST_PROBLEM_ATTRS,
    SERVICE_PROBLEM_ATTRS,
    problem_from,
    problem_sort_key,
)

TOOLSET = "problems"

ProblemState = Literal["warning", "critical", "unknown", "down"]
SERVICE_STATE_CODES = {"warning": 1, "critical": 2, "unknown": 3}

HostPattern = Annotated[
    str | None,
    Field(description="Host name or glob pattern (* and ?), e.g. 'web*' or 'db01'"),
]
ServicePattern = Annotated[
    str | None,
    Field(description="Service name or display name, glob patterns allowed"),
]
HostGroup = Annotated[str | None, Field(description="Only objects of hosts in this host group")]
Limit = Annotated[int, Field(ge=1, le=500, description="Maximum number of entries to return")]


def build(client: IcingaClient, settings: ServerSettings) -> list[ToolSpec]:
    @icinga_tool
    async def list_problems(
        host: HostPattern = None,
        service: ServicePattern = None,
        hostgroup: HostGroup = None,
        states: Annotated[
            list[ProblemState] | None,
            Field(description="Only these states; default: all problem states"),
        ] = None,
        handled: Annotated[
            Literal["unhandled", "handled", "all"],
            Field(
                description="'unhandled' (default) = not acknowledged and not in downtime, "
                "like the Icinga Web problem view"
            ),
        ] = "unhandled",
        hard_only: Annotated[
            bool, Field(description="Only hard states (confirmed after all check retries)")
        ] = False,
        limit: Limit = 50,
    ) -> ProblemList:
        """List current (active) alerts: hosts that are DOWN and services in WARNING,
        CRITICAL or UNKNOWN state. Sorted by severity, newest state changes first.

        The summary always counts all problems matching the host/service/group/state
        filters, so it shows how many handled problems exist even when only unhandled
        ones are returned."""
        wanted = set(states or ["warning", "critical", "unknown", "down"])
        service_codes = sorted(SERVICE_STATE_CODES[s] for s in wanted if s in SERVICE_STATE_CODES)

        raw: list[tuple[str, dict[str, Any]]] = []
        if service_codes:
            flt = Filter()
            flt.add("service.state in service_states", service_states=service_codes)
            _common_filters(flt, "service", host, hostgroup, hard_only)
            if service:
                flt.add(
                    "match(service_pattern, service.name) || "
                    "match(service_pattern, service.display_name)",
                    service_pattern=service,
                )
            services = await client.query_objects(
                "services", filter=flt.expression, filter_vars=flt.vars, attrs=SERVICE_PROBLEM_ATTRS
            )
            raw += [("service", obj) for obj in services]

        if "down" in wanted and not service:
            flt = Filter()
            flt.add("host.state != 0")
            _common_filters(flt, "host", host, hostgroup, hard_only)
            hosts = await client.query_objects(
                "hosts", filter=flt.expression, filter_vars=flt.vars, attrs=HOST_PROBLEM_ATTRS
            )
            raw += [("host", obj) for obj in hosts]

        raw.sort(key=lambda item: problem_sort_key(item[1]))
        problems = [problem_from(obj, kind) for kind, obj in raw]
        summary = ProblemSummary(
            total=len(problems),
            unhandled=sum(not p.handled for p in problems),
            handled=sum(p.handled for p in problems),
            by_state=dict(collections.Counter(p.state for p in problems)),
        )
        selected = [p for p in problems if handled == "all" or p.handled == (handled == "handled")]
        return ProblemList(
            summary=summary,
            returned=min(len(selected), limit),
            truncated=len(selected) > limit,
            problems=selected[:limit],
        )

    return [ToolSpec(list_problems, "list_problems", TOOLSET, "List active problems")]


def _common_filters(
    flt: Filter, prefix: str, host: str | None, hostgroup: str | None, hard_only: bool
) -> None:
    if host:
        flt.add("match(host_pattern, host.name)", host_pattern=host)
    if hostgroup:
        flt.add("hostgroup in host.groups", hostgroup=hostgroup)
    if hard_only:
        flt.add(f"{prefix}.state_type == 1")
