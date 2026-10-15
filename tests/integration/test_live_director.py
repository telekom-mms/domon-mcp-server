"""Read-only Director tests against a real Icinga Web 2 with the Director module."""

from __future__ import annotations

import os
from typing import Any

import pytest
from mcp import Client

from icinga_mcp.config import ServerSettings, load_icinga_settings, load_icingaweb_settings
from icinga_mcp.server import create_server

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.skipif(
        not (os.environ.get("ICINGA_URL") and os.environ.get("ICINGAWEB_URL")),
        reason="ICINGA_URL / ICINGAWEB_URL not set",
    ),
]


async def _call(client: Client, tool: str, args: dict[str, Any]) -> Any:
    result = await client.call_tool(tool, args)
    assert result.is_error is False, result.content
    return result.structured_content


async def test_director_host_and_template() -> None:
    server = create_server(
        load_icinga_settings(),
        ServerSettings(toolsets=["director"]),
        web_settings=load_icingaweb_settings(),
    )
    async with Client(server) as client:
        hosts = await _call(client, "director_list", {"object_type": "host", "limit": 1})
        if not hosts["objects"]:
            pytest.skip("no Director hosts")
        host = hosts["objects"][0]
        details = await _call(client, "director_get", {"object_type": "host", "name": host["name"]})
        assert details["name"] == host["name"]
        assert details["resolved"] is not None
        if host["imports"]:
            template = await _call(
                client, "director_get", {"object_type": "host_template", "name": host["imports"][0]}
            )
            assert (
                any(ref.endswith(":" + host["name"]) for ref in template["referenced_by"])
                or (template["referenced_by_truncated"])
            )
