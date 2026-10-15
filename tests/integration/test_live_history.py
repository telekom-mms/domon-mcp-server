"""Read-only history tests against a real Icinga Web 2 (ICINGAWEB_* and ICINGA_* env)."""

from __future__ import annotations

import os

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


async def test_history_last_day() -> None:
    server = create_server(
        load_icinga_settings(),
        ServerSettings(toolsets=["history"]),
        web_settings=load_icingaweb_settings(),
    )
    async with Client(server) as client:
        result = await client.call_tool(
            "get_history",
            {
                "since": "24h",
                "events": [
                    "state",
                    "notification",
                    "acknowledgement",
                    "downtime",
                    "comment",
                    "flapping",
                ],
                "limit": 20,
            },
        )
    assert result.is_error is False, result.content
    data = result.structured_content
    assert data is not None
    assert data["returned"] <= 20
    assert data["summary"]["total"] >= data["returned"]
    times = [e["time"] for e in data["events"]]
    assert times == sorted(times, reverse=True)
