"""End-to-end tests through the MCP protocol (in-memory client) with a fake Icinga API."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx2
import pytest
from mcp import Client

from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import IcingaSettings, ServerSettings
from icinga_mcp.errors import ConfigError
from icinga_mcp.redaction import REDACTED
from icinga_mcp.server import create_server
from tests.conftest import json_response

pytestmark = pytest.mark.anyio

NOW = time.time()


def _svc(
    host: str, name: str, state: int, *, handled: bool = False, **extra: Any
) -> dict[str, Any]:
    attrs = {
        "name": name,
        "display_name": name.replace("_", " "),
        "host_name": host,
        "state": state,
        "state_type": 1,
        "handled": handled,
        "acknowledgement": 1 if handled else 0,
        "downtime_depth": 0,
        "flapping": False,
        "severity": 0 if state == 0 else (2000 + state * 10 - (1000 if handled else 0)),
        "last_state_change": NOW - 3600,
        "last_check": NOW - 30,
        "last_check_result": {"output": f"{name} output", "performance_data": ["load=1"]},
        **extra,
    }
    return {"name": f"{host}!{name}", "attrs": attrs, "joins": {"host": {"state": 0}}}


SERVICES = [
    _svc("web01", "http", 2),
    _svc("web01", "disk", 1),
    _svc("db01", "mysql", 2, handled=True),
    _svc("web01", "ping", 0),
]
HOSTS = [
    {
        "name": "web01",
        "attrs": {
            "name": "web01",
            "display_name": "Web 01",
            "address": "10.0.0.1",
            "state": 0,
            "state_type": 1,
            "handled": False,
            "problem": False,
            "groups": ["web"],
            "vars": {"os": "Linux", "mail_basic_auth": {"user": "u", "password": "p"}},
            "check_attempt": 1,
            "max_check_attempts": 3,
        },
    },
    {
        "name": "db01",
        "attrs": {"name": "db01", "state": 1, "state_type": 1, "handled": False, "problem": True},
    },
]
COMMENTS = [
    {
        "attrs": {
            "__name": "db01!mysql!c1",
            "host_name": "db01",
            "service_name": "mysql",
            "author": "alice",
            "text": "looking into it",
            "entry_type": 4,
            "entry_time": NOW - 60,
            "expire_time": 0,
        }
    },
]
DOWNTIMES = [
    {
        "attrs": {
            "__name": "web01!dt1",
            "host_name": "web01",
            "service_name": "",
            "author": "bob",
            "comment": "patching",
            "start_time": NOW - 60,
            "end_time": NOW + 3600,
            "fixed": True,
            "duration": 0,
            "trigger_time": NOW - 60,
        }
    },
    {
        "attrs": {
            "__name": "db01!dt2",
            "host_name": "db01",
            "service_name": "",
            "author": "bob",
            "comment": "later",
            "start_time": NOW + 7200,
            "end_time": NOW + 9000,
            "fixed": True,
            "duration": 0,
            "trigger_time": 0,
        }
    },
]
STATUS = {
    "results": [
        {
            "status": {
                "icingaapplication": {
                    "app": {
                        "version": "r2.16.5-1",
                        "node_name": "master-0",
                        "program_start": NOW - 7200,
                        "enable_notifications": True,
                        "enable_host_checks": True,
                        "enable_service_checks": True,
                        "enable_event_handlers": True,
                        "enable_flapping": True,
                    }
                }
            }
        }
    ]
}


class FakeIcinga:
    """Very small stand-in for the Icinga API: evaluates only the filters the tools use."""

    def __init__(self, fail_with: int | None = None) -> None:
        self.bodies: list[tuple[str, dict[str, Any]]] = []
        self.fail_with = fail_with

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if self.fail_with:
            return json_response(self.fail_with, {"error": self.fail_with, "status": "denied"})
        path = request.url.path
        if path == "/v1/status/IcingaApplication":
            return json_response(200, STATUS)
        body = json.loads(request.content or b"{}")
        object_type = path.rsplit("/", 1)[-1]
        self.bodies.append((object_type, body))
        if object_type == "hosts" and "host_name" in (body.get("attrs") or []):
            # Real Icinga answers unknown attributes per object with code 400.
            return json_response(
                200,
                {
                    "results": [
                        {"code": 400, "name": "x", "status": "Invalid field specified: host_name"}
                    ]
                },
            )
        data = {"services": SERVICES, "hosts": HOSTS, "comments": COMMENTS, "downtimes": DOWNTIMES}[
            object_type
        ]
        return json_response(
            200, {"results": [o for o in data if self._match(object_type, o, body)]}
        )

    @staticmethod
    def _match(object_type: str, obj: dict[str, Any], body: dict[str, Any]) -> bool:
        flt = body.get("filter") or ""
        fv = body.get("filter_vars") or {}
        attrs = obj["attrs"]
        host = attrs.get("host_name") or attrs.get("name")
        if "service.state in service_states" in flt and attrs["state"] not in fv["service_states"]:
            return False
        if "host.state != 0" in flt and attrs["state"] == 0:
            return False
        if "host.name == name" in flt and host != fv["name"]:
            return False
        if "host.name == host_name" in flt and (
            host != fv["host_name"] or attrs["name"] != fv["service_name"]
        ):
            return False
        return not ("host_pattern" in fv and host != fv["host_pattern"])


async def _client_for(fake: FakeIcinga, **server: Any) -> tuple[Client, IcingaClient]:
    settings = IcingaSettings(url="https://icinga.example:5665", user="mcp", password="pw")
    icinga = IcingaClient(settings, transport=httpx2.MockTransport(fake))
    mcp_server = create_server(settings, ServerSettings(**server), client=icinga)
    return Client(mcp_server, raise_exceptions=True), icinga


async def _call(fake: FakeIcinga, tool: str, args: dict[str, Any] | None = None) -> Any:
    client, _ = await _client_for(fake)
    async with client:
        result = await client.call_tool(tool, args or {})
    return result


async def test_negotiates_2026_protocol_and_lists_tools() -> None:
    client, _ = await _client_for(FakeIcinga())
    async with client:
        assert client.protocol_version == "2026-07-28"
        assert client.server_info is not None and client.server_info.name == "icinga-mcp"
        tools = (await client.list_tools()).tools
    names = [t.name for t in tools]
    assert names == sorted(names)
    assert set(names) == {
        "get_host",
        "get_service",
        "icinga_status",
        "list_comments",
        "list_downtimes",
        "list_hosts",
        "list_problems",
    }
    for tool in tools:
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
        assert tool.output_schema is not None
        assert tool.description


async def test_legacy_clients_still_work() -> None:
    settings = IcingaSettings(url="https://icinga.example:5665", user="mcp", password="pw")
    icinga = IcingaClient(settings, transport=httpx2.MockTransport(FakeIcinga()))
    server = create_server(settings, ServerSettings(), client=icinga)
    async with Client(server, mode="legacy") as client:
        assert client.protocol_version == "2025-11-25"
        result = await client.call_tool("list_problems", {})
    assert result.is_error is False


async def test_toolset_selection() -> None:
    client, _ = await _client_for(
        FakeIcinga(), toolsets=["problems", "objects"], disabled_tools=["list_hosts"]
    )
    async with client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {"list_problems", "get_host", "get_service"}


async def test_unknown_toolset_rejected() -> None:
    with pytest.raises(ConfigError, match="nope"):
        await _client_for(FakeIcinga(), toolsets=["nope"])


async def test_list_problems_defaults_to_unhandled() -> None:
    fake = FakeIcinga()
    result = await _call(fake, "list_problems")
    data = result.structured_content
    assert data["summary"] == {
        "total": 4,
        "unhandled": 3,
        "handled": 1,
        "by_state": {"CRITICAL": 2, "WARNING": 1, "DOWN": 1},
    }
    problems = data["problems"]
    assert [(p["host"], p["service"]) for p in problems] == [
        ("web01", "http"),
        ("web01", "disk"),
        ("db01", None),
    ]
    assert problems[0]["state"] == "CRITICAL" and problems[0]["duration"] == "1h 0m"
    assert data["truncated"] is False


async def test_list_problems_filters_use_filter_vars() -> None:
    fake = FakeIcinga()
    result = await _call(
        fake,
        "list_problems",
        {"host": "web0*", "states": ["critical"], "handled": "all", "hard_only": True},
    )
    assert result.is_error is False
    assert len(fake.bodies) == 1, "no host query when only service states are requested"
    object_type, body = fake.bodies[0]
    assert object_type == "services"
    assert body["filter_vars"] == {"service_states": [2], "host_pattern": "web0*"}
    assert "web0*" not in body["filter"]
    assert "service.state_type == 1" in body["filter"]


async def test_list_problems_limit() -> None:
    result = await _call(FakeIcinga(), "list_problems", {"handled": "all", "limit": 2})
    data = result.structured_content
    assert data["returned"] == 2 and data["truncated"] is True and data["summary"]["total"] == 4


async def test_get_host_redacts_vars_and_lists_services() -> None:
    result = await _call(FakeIcinga(), "get_host", {"name": "web01"})
    data = result.structured_content
    assert data["vars"] == {"os": "Linux", "mail_basic_auth": REDACTED}
    assert [s["name"] for s in data["services"]] == ["http", "disk", "ping"]
    assert data["service_summary"] == {"CRITICAL": 1, "WARNING": 1, "OK": 1}
    assert data["check_attempt"] == "1/3"
    assert "p" not in json.dumps(data["vars"]).replace(REDACTED, "")


async def test_get_host_not_found_is_tool_error() -> None:
    result = await _call(FakeIcinga(), "get_host", {"name": "nope"})
    assert result.is_error is True
    assert "not found" in result.content[0].text


async def test_get_service() -> None:
    result = await _call(FakeIcinga(), "get_service", {"host": "web01", "service": "http"})
    data = result.structured_content
    assert data["state"] == "CRITICAL"
    assert data["host_state"] == "UP"
    assert data["performance_data"] == ["load=1"]


async def test_icinga_status_counts() -> None:
    data = (await _call(FakeIcinga(), "icinga_status")).structured_content
    assert data["version"] == "r2.16.5-1"
    assert data["hosts"] == {"total": 2, "by_state": {"UP": 1, "DOWN": 1}, "unhandled_problems": 1}
    assert data["services"]["total"] == 4
    assert data["uptime"] == "2h 0m"


async def test_list_comments_and_downtimes() -> None:
    comments = (await _call(FakeIcinga(), "list_comments")).structured_content
    assert comments["comments"][0]["entry_type"] == "acknowledgement"
    downtimes = (await _call(FakeIcinga(), "list_downtimes")).structured_content
    assert [d["in_effect"] for d in downtimes["downtimes"]] == [True, False]
    active = (
        await _call(FakeIcinga(), "list_downtimes", {"in_effect_only": True})
    ).structured_content
    assert [d["name"] for d in active["downtimes"]] == ["web01!dt1"]


async def test_icinga_errors_become_readable_tool_errors() -> None:
    result = await _call(FakeIcinga(fail_with=403), "list_problems")
    assert result.is_error is True
    assert "403" in result.content[0].text and "denied" in result.content[0].text


async def test_invalid_arguments_rejected() -> None:
    result = await _call(FakeIcinga(), "list_problems", {"states": ["broken"]})
    assert result.is_error is True
