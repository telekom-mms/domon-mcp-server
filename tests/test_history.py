"""History via Icinga Web: URL filter building, error handling, event mapping and the tool."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import unquote

import httpx2
import pytest
from mcp import Client

from icinga_mcp import cli
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.clients.icingaweb import IcingaWebClient, any_of, compare, eq
from icinga_mcp.config import IcingaSettings, IcingaWebSettings, ServerSettings
from icinga_mcp.errors import (
    ConfigError,
    IcingaAPIError,
    IcingaAuthError,
    IcingaNotFoundError,
)
from icinga_mcp.history import MonitoringHistory
from icinga_mcp.server import create_server
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio

NOW = time.time()
WEB = IcingaWebSettings(url="https://web.example/", user="jdoe", password="pw")

ROWS: list[dict[str, Any]] = [
    {
        "type": "hard_state",
        "object_type": "service",
        "host_name": "web01",
        "service_description": "http",
        "state": "2",
        "timestamp": str(int(NOW - 600)),
        "output": "[CRITICAL] connection refused",
    },
    {
        "type": "soft_state",
        "object_type": "service",
        "host_name": "web01",
        "service_description": "http",
        "state": "2",
        "timestamp": str(int(NOW - 700)),
        "output": "[ 2/3 ] [CRITICAL] connection refused",
    },
    {
        "type": "notification_state",
        "object_type": "service",
        "host_name": "web01",
        "service_description": "http",
        "state": "2",
        "timestamp": str(int(NOW - 590)),
        "output": "[ops@example.com] [CRITICAL] connection refused",
    },
    {
        "type": "ack",
        "object_type": "service",
        "host_name": "web01",
        "service_description": "http",
        "state": "-1",
        "timestamp": str(int(NOW - 300)),
        "output": "[alice] looking into it",
    },
    {
        "type": "hard_state",
        "object_type": "service",
        "host_name": "web01",
        "service_description": "http",
        "state": "0",
        "timestamp": str(int(NOW - 60)),
        "output": "[OK] fine",
    },
    {
        "type": "hard_state",
        "object_type": "host",
        "host_name": "db01",
        "service_description": None,
        "state": "1",
        "timestamp": str(int(NOW - 120)),
        "output": "PING CRITICAL",
    },
    {
        "type": "something_new",
        "object_type": "host",
        "host_name": "db01",
        "service_description": None,
        "state": "0",
        "timestamp": str(int(NOW - 30)),
        "output": "",
    },
]


class FakeWeb:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = ROWS if rows is None else rows
        self.queries: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.queries.append(request.url.raw_path.decode())
        return json_response(200, self.rows)  # type: ignore[arg-type]


def web_client(handler: Any) -> IcingaWebClient:
    return IcingaWebClient(WEB, transport=httpx2.MockTransport(handler))


def test_filter_values_are_encoded() -> None:
    assert eq("host", "web*") == "host=web*"
    assert eq("host", "a|host=*") == "host=a%7Chost%3D*"
    assert eq("service", "x&(y)!") == "service=x%26%28y%29%21"
    assert compare("timestamp", ">=", 12.9) == "timestamp>=12"
    assert any_of("type", ["ack", "hard_state"]) == "(type=ack|type=hard_state)"
    with pytest.raises(ValueError):
        compare("timestamp", "=", 1)


async def test_events_query_and_mapping() -> None:
    fake = FakeWeb()
    async with web_client(fake) as web:
        events = await MonitoringHistory(web).events(
            start=NOW - 3600,
            end=NOW,
            host="web*",
            service="http",
            categories=["state", "soft_state", "notification", "acknowledgement"],
        )
    query = unquote(fake.queries[0])
    assert query.startswith("/monitoring/list/eventhistory?timestamp>=")
    assert "&timestamp<=" in query and "&host=web*&service=http&" in query
    assert "(type=hard_state|type=soft_state|type=notification_state|type=notify|" in query
    assert query.endswith("&format=json")

    assert [e.event for e in events] == [
        "state change",
        "state change",
        "acknowledged",
        "notification",
        "state change",
        "soft state change",
    ]
    by_event = {e.event + (e.state or ""): e for e in events}
    assert by_event["notificationCRITICAL"].actor == "ops@example.com"
    assert by_event["notificationCRITICAL"].output == "[CRITICAL] connection refused"
    assert by_event["acknowledged"].actor == "alice" and by_event["acknowledged"].state is None
    soft = by_event["soft state changeCRITICAL"]
    assert soft.attempt == "2/3" and soft.output == "[CRITICAL] connection refused"
    assert by_event["state changeDOWN"].service is None
    assert by_event["state changeOK"].output == "[OK] fine", "plugin output keeps its prefix"


@pytest.mark.parametrize(
    ("response", "error", "match"),
    [
        (httpx2.Response(401), IcingaAuthError, "ICINGAWEB_USER"),
        (httpx2.Response(404, json={}), IcingaNotFoundError, "module enabled"),
        (
            httpx2.Response(
                200,
                text="<br /><b>Fatal error</b>: Allowed memory size of 134217728 bytes exhausted",
                headers={"Content-Type": "text/html"},
            ),
            IcingaAPIError,
            "narrow the time window",
        ),
        (
            httpx2.Response(
                200, text="<html><title>Login</title></html>", headers={"Content-Type": "text/html"}
            ),
            IcingaAPIError,
            "HTML instead of JSON: Login",
        ),
        (httpx2.Response(200, json={"error": "x"}), IcingaAPIError, "unexpected answer"),
    ],
)
async def test_web_errors(response: httpx2.Response, error: type[Exception], match: str) -> None:
    async with web_client(lambda request: response) as web:
        with pytest.raises(error, match=match):
            await web.list("/monitoring/list/eventhistory", [])


async def test_rejects_unsafe_path() -> None:
    async with web_client(FakeWeb()) as web:
        with pytest.raises(ValueError):
            await web.list("/monitoring/list?x=1", [])


def _server(fake: FakeWeb | None, **settings: Any) -> Any:
    api = IcingaSettings(url="https://icinga.example:5665", user="u", password="p")
    return create_server(
        api,
        ServerSettings(**settings),
        client=IcingaClient(api, transport=Recorder({}).transport),
        web_client=web_client(fake) if fake else None,
    )


async def test_history_tool_only_with_icinga_web() -> None:
    async with Client(_server(None)) as client:
        assert "get_history" not in {t.name for t in (await client.list_tools()).tools}
    with pytest.raises(ConfigError, match="ICINGAWEB_URL"):
        _server(None, toolsets=["history"])
    async with Client(_server(FakeWeb(), toolsets=["history"])) as client:
        assert [t.name for t in (await client.list_tools()).tools] == ["get_history"]


async def test_get_history_summary_and_limit() -> None:
    async with Client(_server(FakeWeb())) as client:
        result = await client.call_tool(
            "get_history", {"events": ["state", "notification", "acknowledgement"], "limit": 2}
        )
    data = result.structured_content
    assert result.is_error is False and data is not None
    summary = data["summary"]
    assert summary["total"] == 5
    assert summary["state_changes_by_state"] == {"OK": 1, "DOWN": 1, "CRITICAL": 1}
    assert summary["most_active"][0] == {
        "object": "web01!http",
        "state_changes": 2,
        "problem_changes": 1,
        "last_state": "OK",
        "last_change": data["events"][0]["time"],
    }
    assert data["returned"] == 2 and data["truncated"] is True
    assert "timestamp" not in data["events"][0]


@pytest.mark.parametrize(
    ("args", "match"),
    [
        ({"since": "60d"}, "ICINGA_MCP_MAX_HISTORY"),
        ({"since": "yesterday"}, "duration like '24h'"),
        ({"since": "2026-01-02T00:00", "until": "2026-01-01T00:00"}, "after 'since'"),
        ({"until": "soon"}, "until"),
    ],
)
async def test_get_history_validation(args: dict[str, Any], match: str) -> None:
    fake = FakeWeb()
    async with Client(_server(fake)) as client:
        result = await client.call_tool("get_history", args)
    assert result.is_error is True and match in result.content[0].text  # type: ignore[union-attr]
    assert fake.queries == []


async def test_get_history_window_from_iso_times() -> None:
    fake = FakeWeb(rows=[])
    async with Client(_server(fake)) as client:
        result = await client.call_tool(
            "get_history",
            {"since": "2026-10-01T00:00:00+00:00", "until": "2026-10-02T00:00:00+00:00"},
        )
    assert result.is_error is False
    query = unquote(fake.queries[0])
    assert "timestamp>=1790812800&timestamp<=1790899200" in query


async def test_check_reports_icinga_web(settings: IcingaSettings) -> None:
    import io

    api = Recorder(
        {
            ("GET", "/v1/status/IcingaApplication"): json_response(200, {"results": []}),
            ("GET", "/v1"): json_response(200, {"results": [{"permissions": ["*"]}]}),
        }
    )
    out = io.StringIO()
    code = await cli.run_check(
        settings,
        out,
        transport=api.transport,
        web_settings=WEB,
        web_transport=httpx2.MockTransport(FakeWeb()),
    )
    assert code == cli.EXIT_OK
    assert "History      : OK (monitoring module, 3 state changes" in out.getvalue()

    out = io.StringIO()
    code = await cli.run_check(
        settings,
        out,
        transport=api.transport,
        web_settings=WEB,
        web_transport=httpx2.MockTransport(lambda r: httpx2.Response(401)),
    )
    assert code == cli.EXIT_CONNECTION and "History      : ERROR" in out.getvalue()
