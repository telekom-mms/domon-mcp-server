import collections
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from icinga_mcp import formatting as fmt
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import ServerSettings
from icinga_mcp.models import HostBrief, HostDetails, HostList, ServiceDetails
from icinga_mcp.tools.base import Filter, ToolSpec, icinga_tool
from icinga_mcp.tools.convert import (
    HOST_DETAIL_ATTRS,
    SERVICE_DETAIL_ATTRS,
    details_fields,
    problem_sort_key,
    service_brief,
)

TOOLSET = "objects"
MAX_HOST_SERVICES = 200

HostName = Annotated[str, Field(min_length=1, description="Exact Icinga host name")]


def build(client: IcingaClient, settings: ServerSettings) -> list[ToolSpec]:
    @icinga_tool
    async def get_host(name: HostName) -> HostDetails:
        """Details of one host: state, last check output and performance data, check
        timing, groups, templates, custom variables (secrets redacted) and the list of its
        services with their states (problems first)."""
        hosts = await client.query_objects(
            "hosts",
            filter="host.name == name",
            filter_vars={"name": name},
            attrs=HOST_DETAIL_ATTRS,
        )
        if not hosts:
            raise ToolError(f"Host '{name}' not found. Use list_hosts to search for names.")
        attrs = hosts[0].get("attrs", {})
        services = await client.query_objects(
            "services",
            filter="host.name == name",
            filter_vars={"name": name},
            attrs=[
                "name",
                "display_name",
                "state",
                "state_type",
                "handled",
                "severity",
                "last_state_change",
                "last_check_result",
            ],
        )
        services.sort(key=problem_sort_key)
        return HostDetails(
            **details_fields(attrs, is_host=True),
            address=attrs.get("address") or None,
            address6=attrs.get("address6") or None,
            service_summary=dict(
                collections.Counter(
                    fmt.service_state(s.get("attrs", {}).get("state")) for s in services
                )
            ),
            services=[service_brief(s) for s in services[:MAX_HOST_SERVICES]],
            services_truncated=len(services) > MAX_HOST_SERVICES,
        )

    @icinga_tool
    async def get_service(
        host: HostName,
        service: Annotated[str, Field(min_length=1, description="Service name or display name")],
    ) -> ServiceDetails:
        """Details of one service: state, last check output and performance data, check
        command and timing, groups, templates and custom variables (secrets redacted)."""
        results = await client.query_objects(
            "services",
            filter="host.name == host_name && "
            "(service.name == service_name || service.display_name == service_name)",
            filter_vars={"host_name": host, "service_name": service},
            attrs=SERVICE_DETAIL_ATTRS,
            joins=["host.state"],
        )
        if not results:
            raise ToolError(
                f"Service '{service}' on host '{host}' not found. "
                "Use get_host to list the services of a host."
            )
        results.sort(key=lambda r: r.get("attrs", {}).get("name") != service)
        obj = results[0]
        host_join: dict[str, Any] = obj.get("joins", {}).get("host", {})
        return ServiceDetails(
            **details_fields(obj.get("attrs", {}), is_host=False),
            host=host,
            host_state=fmt.host_state(host_join["state"]) if "state" in host_join else None,
        )

    @icinga_tool
    async def list_hosts(
        pattern: Annotated[
            str | None,
            Field(description="Glob pattern matched against name, display name and address"),
        ] = None,
        hostgroup: Annotated[str | None, Field(description="Only hosts in this group")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
    ) -> HostList:
        """Search hosts by name/address pattern or host group. Returns name, address,
        state and groups; use get_host for details."""
        flt = Filter()
        if pattern:
            flt.add(
                "match(pattern, host.name) || match(pattern, host.display_name) || "
                "match(pattern, host.address)",
                pattern=pattern,
            )
        if hostgroup:
            flt.add("hostgroup in host.groups", hostgroup=hostgroup)
        hosts = await client.query_objects(
            "hosts",
            filter=flt.expression,
            filter_vars=flt.vars,
            attrs=["name", "display_name", "address", "state", "handled", "groups"],
        )
        hosts.sort(key=lambda h: str(h.get("attrs", {}).get("name", "")))
        return HostList(
            total=len(hosts),
            returned=min(len(hosts), limit),
            truncated=len(hosts) > limit,
            hosts=[_host_brief(h.get("attrs", {})) for h in hosts[:limit]],
        )

    return [
        ToolSpec(get_host, "get_host", TOOLSET, "Host details"),
        ToolSpec(get_service, "get_service", TOOLSET, "Service details"),
        ToolSpec(list_hosts, "list_hosts", TOOLSET, "Search hosts"),
    ]


def _host_brief(attrs: dict[str, Any]) -> HostBrief:
    return HostBrief(
        name=attrs.get("name", ""),
        display_name=attrs.get("display_name") or attrs.get("name", ""),
        address=attrs.get("address") or None,
        state=fmt.host_state(attrs.get("state")),
        handled=bool(attrs.get("handled")),
        groups=list(attrs.get("groups") or []),
    )
