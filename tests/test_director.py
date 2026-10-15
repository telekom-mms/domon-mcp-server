"""Director tools through MCP with a fake Director REST API."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import httpx2
import pytest
from mcp import Client

from icinga_mcp.clients.director import DirectorClient
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.clients.icingaweb import IcingaWebClient
from icinga_mcp.config import IcingaSettings, IcingaWebSettings, ServerSettings
from icinga_mcp.errors import IcingaNotFoundError
from icinga_mcp.redaction import REDACTED
from icinga_mcp.server import create_server
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

WEB = IcingaWebSettings(url="https://web.example", user="jdoe", password="pw")

LISTS: dict[str, list[dict[str, Any]]] = {
    "/director/hosts": [
        {
            "object_name": "web01",
            "object_type": "object",
            "imports": ["linux"],
            "address": "10.0.0.1",
            "uuid": "u1",
            "fields": [],
            "vars": {"mail_basic_auth": {"user": "u", "password": "s3cret"}, "role": "web"},
        },
        {
            "object_name": "db01",
            "object_type": "object",
            "imports": ["linux"],
            "address": "10.0.0.2",
            "vars": {},
        },
    ],
    "/director/hosts/templates": [
        {
            "object_name": "linux",
            "object_type": "template",
            "check_command": "hostalive",
            "vars": {"hwMonitoringPassword": "hunter2"},
        },
    ],
    "/director/services/templates": [
        {
            "object_name": "generic-http",
            "object_type": "template",
            "check_command": "http",
            "imports": [],
        },
    ],
    "/director/serviceapplyrules": [
        {
            "object_name": "web_http",
            "object_type": "apply",
            "imports": ["generic-http"],
            "assign_filter": 'host.vars.role="web"\n',
            "display_name": "HTTP",
        },
        {
            "object_name": "db_mysql",
            "object_type": "apply",
            "imports": [],
            "assign_filter": 'host.name="db*"',
        },
    ],
    "/director/commands": [
        {
            "object_name": "http",
            "object_type": "external_object",
            "command": "check_http",
            "arguments": {"--password": {"value": "$http_password$"}, "--pass": "plain"},
        },
        {
            "object_name": "hostalive",
            "object_type": "external_object",
            "command": ["check_ping", "-H", "$address$"],
        },
    ],
    "/director/hostgroups": [],
    "/director/notifications/templates": [],
    "/director/timeperiods": [],
}


class FakeDirector:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        raw = request.url.raw_path.decode()
        self.requests.append(raw)
        path = request.url.path
        if path in LISTS:
            return json_response(200, {"objects": LISTS[path]})
        if path == "/director/host" and "resolved" in raw:
            name = unquote(raw.split("name=")[1].split("&")[0])
            obj = next(
                o
                for o in LISTS["/director/hosts"] + LISTS["/director/hosts/templates"]
                if o["object_name"] == name
            )
            return json_response(200, {**obj, "check_command": "hostalive", "zone": "master"})
        return json_response(404, {"error": "not found"})


def _server(fake: FakeDirector) -> Any:
    api = IcingaSettings(url="https://icinga.example:5665", user="u", password="p")
    return create_server(
        api,
        ServerSettings(toolsets=["director"]),
        client=IcingaClient(api, transport=Recorder({}).transport),
        web_client=IcingaWebClient(WEB, transport=httpx2.MockTransport(fake)),
    )


async def _call(fake: FakeDirector, tool: str, args: dict[str, Any]) -> Any:
    async with Client(_server(fake)) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error is False, result.content
    return result.structured_content


async def test_tools_registered() -> None:
    async with Client(_server(FakeDirector())) as client:
        tools = (await client.list_tools()).tools
    assert [t.name for t in tools] == ["director_get", "director_list"]
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


async def test_list_search_matches_assign_filter_and_imports() -> None:
    fake = FakeDirector()
    rules = await _call(
        fake, "director_list", {"object_type": "service_apply_rule", "search": "WEB"}
    )
    assert [o["name"] for o in rules["objects"]] == ["web_http"]
    assert rules["objects"][0]["assign_filter"] == 'host.vars.role="web"'

    hosts = await _call(
        fake, "director_list", {"object_type": "host", "search": "linux", "limit": 1}
    )
    assert hosts["total"] == 2 and hosts["returned"] == 1 and hosts["truncated"] is True
    assert hosts["objects"][0] == {
        "name": "db01",
        "director_type": "object",
        "imports": ["linux"],
        "display_name": None,
        "address": "10.0.0.2",
        "check_command": None,
        "command": None,
        "assign_filter": None,
    }

    commands = await _call(fake, "director_list", {"object_type": "command"})
    assert {o["name"]: o["command"] for o in commands["objects"]} == {
        "hostalive": "check_ping -H $address$",
        "http": "check_http",
    }


async def test_get_host_redacts_and_resolves() -> None:
    fake = FakeDirector()
    data = await _call(fake, "director_get", {"object_type": "host", "name": "web01"})
    assert data["properties"]["vars"] == {
        "mail_basic_auth": {"user": REDACTED, "password": REDACTED},
        "role": "web",
    }
    assert "uuid" not in data["properties"] and "fields" not in data["properties"]
    assert data["resolved"]["check_command"] == "hostalive"
    assert data["referenced_by"] == []


async def test_get_template_and_command_references() -> None:
    fake = FakeDirector()
    template = await _call(fake, "director_get", {"object_type": "host_template", "name": "linux"})
    assert template["referenced_by"] == ["host:db01", "host:web01"]
    assert template["properties"]["vars"] == {"hwMonitoringPassword": REDACTED}

    command = await _call(fake, "director_get", {"object_type": "command", "name": "http"})
    assert command["resolved"] is None
    assert command["properties"]["arguments"] == {
        "--password": {"value": "$http_password$"},
        "--pass": REDACTED,
    }
    assert command["referenced_by"] == ["service_template:generic-http"]


async def test_get_unknown_object() -> None:
    async with Client(_server(FakeDirector())) as client:
        result = await client.call_tool("director_get", {"object_type": "command", "name": "x"})
    assert result.is_error is True
    assert "director_list" in result.content[0].text  # type: ignore[union-attr]


async def test_lists_are_cached() -> None:
    fake = FakeDirector()
    async with Client(_server(fake)) as client:
        await client.call_tool("director_list", {"object_type": "command"})
        await client.call_tool("director_get", {"object_type": "command", "name": "http"})
    assert sum(r.startswith("/director/commands") for r in fake.requests) == 1


async def test_cache_expires() -> None:
    fake = FakeDirector()
    async with IcingaWebClient(WEB, transport=httpx2.MockTransport(fake)) as web:
        director = DirectorClient(web, cache_seconds=0)
        await director.list("command")
        await director.list("command")
    assert len(fake.requests) == 2


async def test_get_json_encodes_parameters() -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.raw_path.decode())
        return json_response(200, {"object_name": "x"})

    async with IcingaWebClient(WEB, transport=httpx2.MockTransport(handler)) as web:
        await web.get_json("/director/host", {"name": "a b&c=d", "resolved": ""})
    assert seen == ["/director/host?name=a%20b%26c%3Dd&resolved"]


async def test_director_not_found_message() -> None:
    async with IcingaWebClient(WEB, transport=httpx2.MockTransport(FakeDirector())) as web:
        with pytest.raises(IcingaNotFoundError, match="not found"):
            await web.get_json("/director/zones")
