from __future__ import annotations

import logging
import re
import ssl
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Self

import httpx2

from icinga_mcp import __version__
from icinga_mcp.config import IcingaSettings
from icinga_mcp.errors import (
    IcingaAPIError,
    IcingaAuthError,
    IcingaConnectionError,
    IcingaNotFoundError,
    IcingaPermissionError,
)

_IDENTIFIER = re.compile(r"^[a-z][a-z-]*$")

logger = logging.getLogger(__name__)

JSON = dict[str, Any]


def build_ssl_context(settings: IcingaSettings) -> ssl.SSLContext | bool:
    if not settings.tls_verification_enabled:
        return False
    cafile = str(settings.ca_cert) if settings.ca_cert else None
    return ssl.create_default_context(cafile=cafile)


class IcingaClient:
    """Async client for the Icinga 2 REST API (``/v1``)."""

    def __init__(
        self,
        settings: IcingaSettings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        if not settings.tls_verification_enabled:
            logger.warning(
                "TLS certificate verification for %s is disabled; set ICINGA_CA_CERT to enable it",
                settings.url,
            )
        self._extensions: dict[str, Any] = {}
        if settings.tls_server_name:
            self._extensions["sni_hostname"] = settings.tls_server_name
        self._http = httpx2.AsyncClient(
            base_url=settings.url,
            auth=httpx2.BasicAuth(settings.user, settings.password.get_secret_value()),
            headers={"Accept": "application/json", "User-Agent": f"icinga-mcp/{__version__}"},
            verify=build_ssl_context(settings),
            timeout=settings.timeout,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def status(self, component: str = "IcingaApplication") -> list[JSON]:
        if not re.fullmatch(r"[A-Za-z]+", component):
            raise ValueError(f"invalid status component: {component!r}")
        return _results(await self._request("GET", f"/v1/status/{component}"))

    async def permissions(self) -> list[str]:
        """Return the permissions of the configured ApiUser."""
        results = _results(await self._request("GET", "/v1"))
        return list(results[0].get("permissions", [])) if results else []

    async def query_objects(
        self,
        object_type: str,
        *,
        filter: str | None = None,
        filter_vars: Mapping[str, Any] | None = None,
        attrs: Sequence[str] | None = None,
        joins: Sequence[str] | None = None,
    ) -> list[JSON]:
        """Query ``/v1/objects/<object_type>`` (plural URL name, e.g. ``services``).

        Pass user-supplied values via ``filter_vars`` instead of formatting them into ``filter``.
        """
        _check_identifier(object_type)
        body: JSON = {}
        if filter:
            body["filter"] = filter
        if filter_vars:
            body["filter_vars"] = dict(filter_vars)
        if attrs is not None:
            body["attrs"] = list(attrs)
        if joins:
            body["joins"] = list(joins)
        payload = await self._request(
            "POST",
            f"/v1/objects/{object_type}",
            json=body,
            headers={"X-HTTP-Method-Override": "GET"},
        )
        results = _results(payload)
        # Icinga reports some query errors (e.g. unknown attributes) per object with HTTP 200.
        for item in results:
            if "attrs" not in item and int(item.get("code") or 0) >= 400:
                raise _api_error(int(item["code"]), item, "")
        return results

    async def action(self, action: str, body: Mapping[str, Any]) -> list[JSON]:
        """Run ``/v1/actions/<action>``. The body must contain ``type`` and a target filter."""
        _check_identifier(action)
        return _results(await self._request("POST", f"/v1/actions/{action}", json=dict(body)))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: JSON | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JSON:
        try:
            response = await self._http.request(
                method, path, json=json, headers=headers, extensions=self._extensions
            )
        except httpx2.TimeoutException as exc:
            raise IcingaConnectionError(
                f"timeout after {self._settings.timeout}s talking to {self._settings.url}"
            ) from exc
        except httpx2.ConnectError as exc:
            raise IcingaConnectionError(_connect_hint(self._settings, exc)) from exc
        except httpx2.TransportError as exc:
            raise IcingaConnectionError(f"transport error: {exc}") from exc

        payload = _json_or_none(response)
        if response.is_success:
            return payload if payload is not None else {}
        raise _api_error(response.status_code, payload, response.text)


def _check_identifier(value: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid Icinga API identifier: {value!r}")


def _results(payload: JSON) -> list[JSON]:
    results = payload.get("results", [])
    return results if isinstance(results, list) else []


def _json_or_none(response: httpx2.Response) -> JSON | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _api_error(status_code: int, payload: JSON | None, text: str) -> IcingaAPIError:
    message = ""
    if payload:
        message = str(payload.get("status") or payload.get("error") or "")
        if not message and payload.get("results"):
            first = payload["results"][0]
            message = str(first.get("status") or first.get("errors") or "")
    message = message or text.strip()[:200] or "no details"
    error_cls: type[IcingaAPIError] = {
        401: IcingaAuthError,
        403: IcingaPermissionError,
        404: IcingaNotFoundError,
    }.get(status_code, IcingaAPIError)
    return error_cls(status_code, message)


def _connect_hint(settings: IcingaSettings, exc: Exception) -> str:
    detail = str(exc) or f"{type(exc).__name__} (connection closed)"
    if "CERTIFICATE_VERIFY_FAILED" in detail or "certificate" in detail.lower():
        return (
            f"TLS verification failed for {settings.url}: {detail}. "
            "Set ICINGA_CA_CERT to the Icinga CA and, if the certificate name differs "
            "from the URL host, ICINGA_TLS_SERVER_NAME."
        )
    hint = ""
    if settings.tls_server_name:
        hint = (
            f" Check ICINGA_TLS_SERVER_NAME ({settings.tls_server_name}): proxies that route "
            "by SNI drop connections for unknown names."
        )
    return f"cannot connect to {settings.url}: {detail}.{hint}"
