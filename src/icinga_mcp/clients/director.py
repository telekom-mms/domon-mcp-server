"""Read-only access to the Icinga Director REST API (``/director/...``) through Icinga Web."""

from __future__ import annotations

import time
from typing import Any, Literal

from icinga_mcp.clients.icingaweb import IcingaWebClient
from icinga_mcp.errors import IcingaAPIError

ObjectType = Literal[
    "host",
    "host_template",
    "service_template",
    "service_apply_rule",
    "command",
    "hostgroup",
    "notification_template",
    "timeperiod",
]

# object type -> (list endpoint, single-object endpoint supporting ?resolved, or None)
ENDPOINTS: dict[str, tuple[str, str | None]] = {
    "host": ("/director/hosts", "/director/host"),
    "host_template": ("/director/hosts/templates", "/director/host"),
    "service_template": ("/director/services/templates", "/director/service"),
    "service_apply_rule": ("/director/serviceapplyrules", None),
    "command": ("/director/commands", None),
    "hostgroup": ("/director/hostgroups", None),
    "notification_template": ("/director/notifications/templates", None),
    "timeperiod": ("/director/timeperiods", None),
}

CACHE_SECONDS = 60.0


class DirectorClient:
    """Lists are cached briefly: Director answers are large (hundreds of commands and rules)
    and one tool call often needs several of them."""

    def __init__(self, web: IcingaWebClient, cache_seconds: float = CACHE_SECONDS) -> None:
        self._web = web
        self._cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def list(self, object_type: str) -> list[dict[str, Any]]:
        path = ENDPOINTS[object_type][0]
        cached = self._cache.get(path)
        now = time.monotonic()
        if cached and now - cached[0] < self._cache_seconds:
            return cached[1]
        data = await self._web.get_json(path)
        objects = data.get("objects") if isinstance(data, dict) else None
        if not isinstance(objects, list):
            raise IcingaAPIError(200, f"unexpected Director answer from {path}: {str(data)[:200]}")
        self._cache[path] = (now, objects)
        return objects

    async def resolved(self, object_type: str, name: str) -> dict[str, Any] | None:
        """The object with all properties inherited from its templates, if supported."""
        path = ENDPOINTS[object_type][1]
        if path is None:
            return None
        data = await self._web.get_json(path, {"name": name, "resolved": ""})
        return data if isinstance(data, dict) else None
