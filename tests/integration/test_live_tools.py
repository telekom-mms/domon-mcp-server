"""Calls every read tool through the MCP protocol against a real Icinga 2 instance.

Read-only. Run with ``uv run pytest -m integration`` and the ``ICINGA_*`` variables set.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from mcp import Client

from icinga_mcp.config import ServerSettings, load_icinga_settings
from icinga_mcp.server import create_server

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("ICINGA_URL"), reason="ICINGA_URL not set"),
]


async def _call(client: Client, tool: str, args: dict[str, Any] | None = None) -> Any:
    result = await client.call_tool(tool, args or {})
    assert result.is_error is False, result.content
    return result.structured_content


async def test_all_read_tools() -> None:
    server = create_server(load_icinga_settings(), ServerSettings())
    async with Client(server) as client:
        status = await _call(client, "icinga_status")
        assert status["hosts"]["total"] > 0

        problems = await _call(client, "list_problems", {"handled": "all", "limit": 5})
        assert problems["summary"]["total"] >= problems["returned"]

        hosts = await _call(client, "list_hosts", {"limit": 1})
        host = hosts["hosts"][0]["name"]
        details = await _call(client, "get_host", {"name": host})
        assert details["name"] == host

        if details["services"]:
            service = details["services"][0]["name"]
            svc = await _call(client, "get_service", {"host": host, "service": service})
            assert svc["name"] == service and svc["host"] == host

        down = await _call(client, "list_problems", {"states": ["down"], "handled": "all"})
        assert all(p["type"] == "host" for p in down["problems"])

        await _call(client, "list_comments", {"limit": 5})
        await _call(client, "list_downtimes", {"limit": 5})
