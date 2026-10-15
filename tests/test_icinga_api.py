from __future__ import annotations

import base64
import json
import logging
import ssl
from pathlib import Path

import httpx2
import pytest

from icinga_mcp.clients.icinga_api import IcingaClient, build_ssl_context
from icinga_mcp.config import IcingaSettings
from icinga_mcp.errors import (
    IcingaAPIError,
    IcingaAuthError,
    IcingaConnectionError,
    IcingaNotFoundError,
    IcingaPermissionError,
)
from tests.conftest import Recorder, json_response

pytestmark = pytest.mark.anyio


async def test_query_objects_uses_method_override(settings: IcingaSettings) -> None:
    rec = Recorder(
        {
            ("POST", "/v1/objects/services"): json_response(
                200, {"results": [{"name": "h!s", "attrs": {"state": 2}}]}
            )
        }
    )
    async with IcingaClient(settings, transport=rec.transport) as client:
        results = await client.query_objects(
            "services",
            filter="host.name == h",
            filter_vars={"h": "web01"},
            attrs=["state"],
            joins=["host.name"],
        )

    assert results == [{"name": "h!s", "attrs": {"state": 2}}]
    req = rec.requests[0]
    assert req.headers["X-HTTP-Method-Override"] == "GET"
    assert req.headers["Accept"] == "application/json"
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(b"mcp:s3cret").decode()
    assert json.loads(req.content) == {
        "filter": "host.name == h",
        "filter_vars": {"h": "web01"},
        "attrs": ["state"],
        "joins": ["host.name"],
    }


async def test_status_and_permissions(settings: IcingaSettings) -> None:
    rec = Recorder(
        {
            ("GET", "/v1/status/IcingaApplication"): json_response(
                200, {"results": [{"name": "IcingaApplication", "status": {}}]}
            ),
            ("GET", "/v1"): json_response(200, {"results": [{"permissions": ["objects/query/*"]}]}),
        }
    )
    async with IcingaClient(settings, transport=rec.transport) as client:
        assert (await client.status())[0]["name"] == "IcingaApplication"
        assert await client.permissions() == ["objects/query/*"]


async def test_action_posts_body(settings: IcingaSettings) -> None:
    rec = Recorder(
        {
            ("POST", "/v1/actions/add-comment"): json_response(
                200, {"results": [{"code": 200, "status": "ok"}]}
            )
        }
    )
    body = {
        "type": "Host",
        "filter": "host.name == h",
        "filter_vars": {"h": "x"},
        "author": "a",
        "comment": "c",
    }
    async with IcingaClient(settings, transport=rec.transport) as client:
        assert (await client.action("add-comment", body))[0]["code"] == 200
    assert json.loads(rec.requests[0].content) == body
    assert "X-HTTP-Method-Override" not in rec.requests[0].headers


async def test_sni_hostname_extension(settings: IcingaSettings) -> None:
    settings = settings.model_copy(update={"tls_server_name": "master-0.svc.cluster.local"})
    rec = Recorder({("GET", "/v1"): json_response(200, {"results": [{"permissions": []}]})})
    async with IcingaClient(settings, transport=rec.transport) as client:
        await client.permissions()
    assert rec.requests[0].extensions["sni_hostname"] == "master-0.svc.cluster.local"


@pytest.mark.parametrize(
    ("status", "error_cls"),
    [
        (401, IcingaAuthError),
        (403, IcingaPermissionError),
        (404, IcingaNotFoundError),
        (500, IcingaAPIError),
    ],
)
async def test_error_mapping(
    settings: IcingaSettings, status: int, error_cls: type[IcingaAPIError]
) -> None:
    rec = Recorder({("GET", "/v1"): json_response(status, {"error": status, "status": "nope"})})
    async with IcingaClient(settings, transport=rec.transport) as client:
        with pytest.raises(error_cls, match="nope") as info:
            await client.permissions()
    assert info.value.status_code == status


async def test_error_with_non_json_body(settings: IcingaSettings) -> None:
    rec = Recorder({("GET", "/v1"): httpx2.Response(502, content=b"Bad Gateway")})
    async with IcingaClient(settings, transport=rec.transport) as client:
        with pytest.raises(IcingaAPIError, match="Bad Gateway"):
            await client.permissions()


async def test_action_keeps_per_object_errors(settings: IcingaSettings) -> None:
    rec = Recorder(
        {
            ("POST", "/v1/actions/schedule-downtime"): json_response(
                500, {"results": [{"code": 500, "status": "Invalid end time"}]}
            )
        }
    )
    async with IcingaClient(settings, transport=rec.transport) as client:
        results = await client.action(
            "schedule-downtime", {"type": "Host", "filter": "host.name in names"}
        )
    assert results == [{"code": 500, "status": "Invalid end time"}]


async def test_action_permission_error_raises(settings: IcingaSettings) -> None:
    rec = Recorder(
        {
            ("POST", "/v1/actions/add-comment"): json_response(
                403, {"results": [{"code": 403, "status": "No permission"}]}
            )
        }
    )
    async with IcingaClient(settings, transport=rec.transport) as client:
        with pytest.raises(IcingaPermissionError):
            await client.action("add-comment", {"type": "Host", "filter": "host.name in names"})


async def test_action_without_filter_is_refused(settings: IcingaSettings) -> None:
    rec = Recorder({})
    async with IcingaClient(settings, transport=rec.transport) as client:
        with pytest.raises(ValueError, match="without a filter"):
            await client.action("add-comment", {"type": "Host", "author": "a", "comment": "c"})
    assert rec.requests == []


async def test_connect_error_mentions_tls_settings(settings: IcingaSettings) -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate")

    async with IcingaClient(settings, transport=httpx2.MockTransport(fail)) as client:
        with pytest.raises(IcingaConnectionError, match="ICINGA_CA_CERT"):
            await client.permissions()


async def test_timeout(settings: IcingaSettings) -> None:
    def slow(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out")

    async with IcingaClient(settings, transport=httpx2.MockTransport(slow)) as client:
        with pytest.raises(IcingaConnectionError, match="timeout"):
            await client.permissions()


@pytest.mark.parametrize("bad", ["../status", "Services", "hosts/x", ""])
async def test_rejects_unsafe_identifiers(settings: IcingaSettings, bad: str) -> None:
    async with IcingaClient(settings, transport=Recorder({}).transport) as client:
        with pytest.raises(ValueError, match="identifier"):
            await client.query_objects(bad)


async def test_empty_connect_error_hints_sni(settings: IcingaSettings) -> None:
    settings = settings.model_copy(update={"tls_server_name": "master-0.svc"})

    def dropped(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("")

    async with IcingaClient(settings, transport=httpx2.MockTransport(dropped)) as client:
        with pytest.raises(IcingaConnectionError, match="ICINGA_TLS_SERVER_NAME"):
            await client.permissions()


async def test_warns_when_tls_unverified(
    settings: IcingaSettings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        async with IcingaClient(settings, transport=Recorder({}).transport):
            pass
    assert "verification" in caplog.text
    assert "ICINGA_CA_CERT" in caplog.text
    assert build_ssl_context(settings) is False


def test_ssl_context_with_system_trust(settings: IcingaSettings) -> None:
    ctx = build_ssl_context(settings.model_copy(update={"verify_tls": True}))
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_ssl_context_rejects_invalid_ca(settings: IcingaSettings, tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("not a certificate")
    with pytest.raises(ssl.SSLError):
        build_ssl_context(settings.model_copy(update={"ca_cert": ca}))


async def test_per_object_query_error_is_raised(settings: IcingaSettings) -> None:
    rec = Recorder(
        {
            ("POST", "/v1/objects/hosts"): json_response(
                200,
                {"results": [{"code": 400, "name": "h", "status": "Invalid field specified: x"}]},
            )
        }
    )
    async with IcingaClient(settings, transport=rec.transport) as client:
        with pytest.raises(IcingaAPIError, match="Invalid field specified") as info:
            await client.query_objects("hosts", attrs=["x"])
    assert info.value.status_code == 400
