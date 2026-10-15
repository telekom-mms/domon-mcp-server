"""Write tools through the MCP protocol: targeting, safety checks and confirmation flows."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import mcp.types as t
import pytest
from mcp import Client

from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import IcingaSettings, ServerSettings
from icinga_mcp.errors import ConfigError
from icinga_mcp.server import create_server
from tests.conftest import json_response

pytestmark = pytest.mark.anyio

WRITE_TOOLS = {
    "acknowledge_problem",
    "remove_acknowledgement",
    "add_comment",
    "schedule_downtime",
    "remove_downtime",
}


def _obj(name: str, state: int, *, problem: bool, ack: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "attrs": {
            "state": state,
            "problem": problem,
            "acknowledgement": 1 if ack else 0,
            "downtime_depth": 0,
        },
    }


OBJECTS: dict[str, list[dict[str, Any]]] = {
    "hosts": [_obj("web01", 0, problem=False), _obj("db01", 1, problem=True)],
    "services": [
        _obj("web01!http", 2, problem=True),
        _obj("web01!disk", 1, problem=True, ack=True),
        _obj("web01!ping", 0, problem=False),
    ],
    "downtimes": [
        {
            "name": "web01!dt1",
            "attrs": {
                "__name": "web01!dt1",
                "host_name": "web01",
                "service_name": "",
                "author": "bob",
                "comment": "patching",
            },
        }
    ],
}


class FakeIcinga:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.actions: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail or set()

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content or b"{}")
        kind, name = request.url.path.split("/")[2:4]
        names = set((body.get("filter_vars") or {}).get("names", []))
        if kind == "objects":
            return json_response(200, {"results": [o for o in OBJECTS[name] if o["name"] in names]})
        self.actions.append((name, body))
        results = [
            {"code": 500, "status": f"failed for {n}"}
            if n in self.fail
            else {"code": 200, "status": f"ok {n}", "name": f"{n}!new-downtime"}
            for n in sorted(names)
        ]
        return json_response(200, {"results": results})


class Asker:
    """Elicitation callback that records the questions and answers them."""

    def __init__(self, action: str = "accept", approve: bool = True) -> None:
        self.action = action
        self.approve = approve
        self.messages: list[str] = []

    async def __call__(self, context: Any, params: t.ElicitRequestParams) -> t.ElicitResult:
        self.messages.append(params.message)
        if self.action == "accept":
            return t.ElicitResult(action="accept", content={"approve": self.approve})
        return t.ElicitResult(action=self.action)


def _server(fake: FakeIcinga, **settings: Any) -> Any:
    icinga_settings = IcingaSettings(url="https://icinga.example:5665", user="u", password="p")
    client = IcingaClient(icinga_settings, transport=httpx2.MockTransport(fake))
    server_settings = ServerSettings(**{"read_only": False, "author": "alice", **settings})
    return create_server(icinga_settings, server_settings, client=client)


async def _call(
    fake: FakeIcinga,
    tool: str,
    args: dict[str, Any],
    *,
    asker: Asker | None = None,
    mode: str = "auto",
    **settings: Any,
) -> t.CallToolResult:
    async with Client(_server(fake, **settings), mode=mode, elicitation_callback=asker) as client:
        return await client.call_tool(tool, args)


def _text(result: t.CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, t.TextContent)
    return block.text


async def test_write_tools_hidden_by_default() -> None:
    icinga_settings = IcingaSettings(url="https://icinga.example:5665", user="u", password="p")
    server = create_server(
        icinga_settings,
        ServerSettings(),
        client=IcingaClient(icinga_settings, transport=httpx2.MockTransport(FakeIcinga())),
    )
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert not names & WRITE_TOOLS


async def test_write_tools_need_author() -> None:
    with pytest.raises(ConfigError, match="ICINGA_MCP_AUTHOR"):
        _server(FakeIcinga(), author=None)


async def test_write_tools_annotations() -> None:
    async with Client(_server(FakeIcinga())) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    for name in WRITE_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is False
        assert annotations.destructive_hint is False
        schema_props = tools[name].input_schema["properties"]
        assert "approved" not in schema_props and "resolved" not in schema_props
        assert "confirm" in schema_props


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_acknowledge_with_elicitation(mode: str) -> None:
    fake, asker = FakeIcinga(), Asker()
    result = await _call(
        fake,
        "acknowledge_problem",
        {"targets": ["web01!http", "web01!disk", "web01!ping", "db01"], "comment": "INC-1"},
        asker=asker,
        mode=mode,
    )
    data = result.structured_content
    assert result.is_error is False and data is not None
    assert data["applied"] is True
    assert data["targets"] == ["db01", "web01!http"]
    assert {s["target"]: s["reason"] for s in data["skipped"]} == {
        "web01!disk": "already acknowledged",
        "web01!ping": "not a problem (OK)",
    }
    assert len(asker.messages) == 1
    assert "db01" in asker.messages[0] and "INC-1" in asker.messages[0]
    assert "web01!ping" not in asker.messages[0]
    assert [(a, b["type"], b["filter_vars"]["names"]) for a, b in fake.actions] == [
        ("acknowledge-problem", "Host", ["db01"]),
        ("acknowledge-problem", "Service", ["web01!http"]),
    ]
    body = fake.actions[0][1]
    assert body["author"] == "alice" and body["comment"] == "INC-1"
    assert body["notify"] is True and body["sticky"] is False and "expiry" not in body


async def test_user_declines() -> None:
    fake = FakeIcinga()
    result = await _call(
        fake, "acknowledge_problem", {"targets": ["db01"], "comment": "x"}, asker=Asker("decline")
    )
    assert result.is_error is True
    assert fake.actions == []


async def test_user_answers_no() -> None:
    fake = FakeIcinga()
    result = await _call(
        fake,
        "add_comment",
        {"targets": ["db01"], "comment": "x", "confirm": True},
        asker=Asker(approve=False),
    )
    assert result.structured_content is not None
    assert result.structured_content["applied"] is False
    assert fake.actions == []


async def test_model_cannot_skip_question_with_confirm() -> None:
    fake, asker = FakeIcinga(), Asker("decline")
    await _call(
        fake, "add_comment", {"targets": ["db01"], "comment": "x", "confirm": True}, asker=asker
    )
    assert len(asker.messages) == 1
    assert fake.actions == []


async def test_two_step_confirmation_without_elicitation() -> None:
    fake = FakeIcinga()
    args: dict[str, Any] = {"targets": ["db01"], "comment": "checked"}
    preview = await _call(fake, "add_comment", args)
    assert preview.is_error is False and preview.structured_content is not None
    assert preview.structured_content["applied"] is False
    assert "confirm=true" in preview.structured_content["message"]
    assert fake.actions == []

    applied = await _call(fake, "add_comment", {**args, "confirm": True})
    assert applied.structured_content is not None
    assert applied.structured_content["applied"] is True
    assert len(fake.actions) == 1


async def test_require_elicitation_refuses_fallback() -> None:
    fake = FakeIcinga()
    result = await _call(
        fake,
        "add_comment",
        {"targets": ["db01"], "comment": "x", "confirm": True},
        require_elicitation=True,
    )
    assert result.is_error is True and "ICINGA_MCP_REQUIRE_ELICITATION" in _text(result)
    assert fake.actions == []


async def test_unknown_target_aborts_before_asking() -> None:
    fake, asker = FakeIcinga(), Asker()
    result = await _call(
        fake, "add_comment", {"targets": ["db01", "nope!svc"], "comment": "x"}, asker=asker
    )
    assert result.is_error is True and "nope!svc" in _text(result)
    assert asker.messages == [] and fake.actions == []


async def test_too_many_targets() -> None:
    fake = FakeIcinga()
    result = await _call(
        fake,
        "add_comment",
        {"targets": ["db01", "web01"], "comment": "x", "confirm": True},
        max_targets=1,
    )
    assert result.is_error is True and "ICINGA_MCP_MAX_TARGETS" in _text(result)
    assert fake.actions == []


async def test_nothing_to_acknowledge_needs_no_question() -> None:
    fake, asker = FakeIcinga(), Asker()
    result = await _call(
        fake, "acknowledge_problem", {"targets": ["web01!ping"], "comment": "x"}, asker=asker
    )
    assert result.structured_content is not None
    assert result.structured_content["message"] == "Nothing to do."
    assert asker.messages == [] and fake.actions == []


async def test_acknowledge_expiry_and_invalid_duration() -> None:
    fake = FakeIcinga()
    await _call(
        fake,
        "acknowledge_problem",
        {"targets": ["db01"], "comment": "x", "expire_in": "4h", "sticky": True, "confirm": True},
    )
    body = fake.actions[0][1]
    assert body["sticky"] is True and body["expiry"] > 0

    bad = await _call(
        fake, "acknowledge_problem", {"targets": ["db01"], "comment": "x", "expire_in": "soon"}
    )
    assert bad.is_error is True and "expire_in" in _text(bad)


async def test_remove_acknowledgement_only_acked() -> None:
    fake = FakeIcinga()
    result = await _call(
        fake, "remove_acknowledgement", {"targets": ["web01!disk", "db01"], "confirm": True}
    )
    data = result.structured_content
    assert data is not None and data["targets"] == ["web01!disk"]
    assert data["skipped"] == [{"target": "db01", "reason": "not acknowledged"}]
    assert fake.actions == [
        (
            "remove-acknowledgement",
            {
                "type": "Service",
                "filter": "service.__name in names",
                "filter_vars": {"names": ["web01!disk"]},
                "author": "alice",
            },
        )
    ]


async def test_schedule_fixed_downtime_with_all_services() -> None:
    fake, asker = FakeIcinga(), Asker()
    result = await _call(
        fake,
        "schedule_downtime",
        {
            "targets": ["web01", "web01!http"],
            "comment": "patch",
            "duration": "2h",
            "all_services": True,
        },
        asker=asker,
    )
    data = result.structured_content
    assert data is not None and data["applied"] is True
    assert {r["name"] for r in data["results"]} == {
        "web01!new-downtime",
        "web01!http!new-downtime",
    }
    host_call, service_call = fake.actions
    assert host_call[1]["type"] == "Host" and host_call[1]["all_services"] is True
    assert service_call[1]["type"] == "Service" and "all_services" not in service_call[1]
    body = host_call[1]
    assert body["end_time"] - body["start_time"] == 7200
    assert body["fixed"] is True and body["duration"] == 7200
    assert "now until start + 2h" in asker.messages[0]


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"duration": "2h", "end": "2099-01-01T00:00"}, "exactly one"),
        ({}, "exactly one"),
        ({"start": "2020-01-01T00:00", "end": "2020-01-02T00:00"}, "already over"),
        ({"start": "2099-01-02T00:00", "end": "2099-01-01T00:00"}, "end after it starts"),
        ({"duration": "30d"}, "ICINGA_MCP_MAX_DOWNTIME"),
        ({"duration": "2h", "fixed": False}, "flexible_duration"),
        ({"duration": "1h", "fixed": False, "flexible_duration": "2h"}, "must not be longer"),
        ({"start": "tomorrow", "duration": "1h"}, "ISO 8601"),
    ],
)
async def test_schedule_downtime_validation(args: dict[str, Any], error: str) -> None:
    fake, asker = FakeIcinga(), Asker()
    result = await _call(
        fake,
        "schedule_downtime",
        {"targets": ["web01"], "comment": "x", **args},
        asker=asker,
    )
    assert result.is_error is True and error in _text(result)
    assert asker.messages == [] and fake.actions == []


async def test_flexible_downtime() -> None:
    fake = FakeIcinga()
    await _call(
        fake,
        "schedule_downtime",
        {
            "targets": ["web01"],
            "comment": "x",
            "start": "2099-01-01T08:00+00:00",
            "end": "2099-01-01T12:00+00:00",
            "fixed": False,
            "flexible_duration": "1h",
            "confirm": True,
        },
    )
    body = fake.actions[0][1]
    assert body["fixed"] is False and body["duration"] == 3600
    assert body["end_time"] - body["start_time"] == 4 * 3600


async def test_remove_downtime() -> None:
    fake, asker = FakeIcinga(), Asker()
    result = await _call(fake, "remove_downtime", {"names": ["web01!dt1"]}, asker=asker)
    assert result.structured_content is not None
    assert result.structured_content["applied"] is True
    assert "patching" in asker.messages[0]
    assert fake.actions == [
        (
            "remove-downtime",
            {
                "type": "Downtime",
                "filter": "downtime.__name in names",
                "filter_vars": {"names": ["web01!dt1"]},
            },
        )
    ]

    unknown = await _call(fake, "remove_downtime", {"names": ["web01!nope"]}, asker=asker)
    assert unknown.is_error is True and "list_downtimes" in _text(unknown)


async def test_partial_failure_is_reported() -> None:
    fake = FakeIcinga(fail={"web01!http"})
    result = await _call(
        fake, "add_comment", {"targets": ["db01", "web01!http"], "comment": "x", "confirm": True}
    )
    data = result.structured_content
    assert data is not None and data["applied"] is True
    assert "1 of 2 succeeded" in data["message"] and "failed for web01!http" in data["message"]
