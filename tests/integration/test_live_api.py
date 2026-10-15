"""Read-only tests against a real Icinga 2 instance.

Run with ``uv run pytest -m integration`` and the ``ICINGA_*`` variables set.
These tests never change anything in Icinga.
"""

from __future__ import annotations

import os

import pytest

from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import IcingaSettings, load_icinga_settings
from icinga_mcp.errors import IcingaConnectionError
from icinga_mcp.permissions import READ_PERMISSIONS, missing

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.skipif(not os.environ.get("ICINGA_URL"), reason="ICINGA_URL not set"),
]


@pytest.fixture
def live_settings() -> IcingaSettings:
    return load_icinga_settings()


async def test_status(live_settings: IcingaSettings) -> None:
    async with IcingaClient(live_settings) as client:
        status = await client.status()
    app = status[0]["status"]["icingaapplication"]["app"]
    assert app["version"].startswith("r2.")


async def test_read_permissions(live_settings: IcingaSettings) -> None:
    async with IcingaClient(live_settings) as client:
        granted = await client.permissions()
    assert missing(READ_PERMISSIONS, granted) == []


async def test_query_problems_with_filter_vars(live_settings: IcingaSettings) -> None:
    async with IcingaClient(live_settings) as client:
        problems = await client.query_objects(
            "services",
            filter="service.state >= min_state",
            filter_vars={"min_state": 1},
            attrs=["state", "acknowledgement"],
            joins=["host.name"],
        )
        none = await client.query_objects(
            "hosts",
            filter="host.name == name",
            filter_vars={"name": "icinga-mcp-does-not-exist"},
            attrs=["state"],
        )
    assert all(p["attrs"]["state"] >= 1 for p in problems)
    assert all("host" in p["joins"] for p in problems)
    assert none == []


async def test_tls_server_name_is_enforced(live_settings: IcingaSettings) -> None:
    if not (live_settings.tls_verification_enabled and live_settings.tls_server_name):
        pytest.skip("only relevant when ICINGA_TLS_SERVER_NAME is set")
    wrong = live_settings.model_copy(update={"tls_server_name": "wrong.example.invalid"})
    async with IcingaClient(wrong) as client:
        with pytest.raises(IcingaConnectionError, match=r"TLS|ICINGA_TLS_SERVER_NAME"):
            await client.permissions()
