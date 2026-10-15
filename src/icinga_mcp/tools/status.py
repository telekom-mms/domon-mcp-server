import collections
import time
from typing import Any

from icinga_mcp import formatting as fmt
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.models import IcingaStatus, StateCounts
from icinga_mcp.tools.base import ToolSpec, icinga_tool

TOOLSET = "status"


def build(client: IcingaClient) -> list[ToolSpec]:
    @icinga_tool
    async def icinga_status() -> IcingaStatus:
        """Overview of the Icinga 2 instance: version, global feature flags and the number
        of hosts and services per state, including unhandled problems.

        Start here to get a quick picture of the monitoring health."""
        status = await client.status("IcingaApplication")
        app: dict[str, Any] = (
            status[0].get("status", {}).get("icingaapplication", {}).get("app", {})
            if status
            else {}
        )
        attrs = ["state", "handled", "problem"]
        hosts = await client.query_objects("hosts", attrs=attrs)
        services = await client.query_objects("services", attrs=attrs)
        start = app.get("program_start")
        return IcingaStatus(
            version=app.get("version"),
            node=app.get("node_name"),
            program_start=fmt.timestamp(start),
            uptime=fmt.human_duration(int(time.time() - float(start))) if start else None,
            notifications_enabled=app.get("enable_notifications"),
            active_host_checks_enabled=app.get("enable_host_checks"),
            active_service_checks_enabled=app.get("enable_service_checks"),
            event_handlers_enabled=app.get("enable_event_handlers"),
            flap_detection_enabled=app.get("enable_flapping"),
            hosts=_counts(hosts, fmt.host_state),
            services=_counts(services, fmt.service_state),
        )

    return [ToolSpec(icinga_status, "icinga_status", TOOLSET, "Icinga status overview")]


def _counts(objects: list[dict[str, Any]], state_name: Any) -> StateCounts:
    by_state = collections.Counter(state_name(o.get("attrs", {}).get("state")) for o in objects)
    unhandled = sum(
        1
        for o in objects
        if o.get("attrs", {}).get("problem") and not o.get("attrs", {}).get("handled")
    )
    return StateCounts(total=len(objects), by_state=dict(by_state), unhandled_problems=unhandled)
