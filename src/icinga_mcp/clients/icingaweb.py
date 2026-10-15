from __future__ import annotations

import html
import re
import ssl
from collections.abc import Sequence
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote

import httpx2

from icinga_mcp import __version__
from icinga_mcp.config import IcingaWebSettings
from icinga_mcp.errors import (
    IcingaAPIError,
    IcingaAuthError,
    IcingaConnectionError,
    IcingaNotFoundError,
    IcingaPermissionError,
)

_TAGS = re.compile(r"<[^>]+>")
_PATH = re.compile(r"^/[a-z0-9/_-]+$")


def eq(column: str, value: str) -> str:
    """Icinga Web URL filter ``column=value``; ``*`` stays a wildcard, everything else is
    percent-encoded so values cannot change the filter structure."""
    return f"{column}={quote(value, safe='*')}"


def compare(column: str, operator: str, number: float) -> str:
    if operator not in {">=", "<=", ">", "<"}:
        raise ValueError(f"unsupported operator {operator!r}")
    return f"{column}{operator}{int(number)}"


def any_of(column: str, values: Sequence[str]) -> str:
    return "(" + "|".join(eq(column, value) for value in values) + ")"


class IcingaWebClient:
    """Read-only JSON access to Icinga Web 2 list views (``?format=json``)."""

    def __init__(
        self, settings: IcingaWebSettings, *, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        verify: ssl.SSLContext | bool = False
        if settings.verify_tls:
            verify = ssl.create_default_context(
                cafile=str(settings.ca_cert) if settings.ca_cert else None
            )
        self._http = httpx2.AsyncClient(
            base_url=settings.url,
            auth=httpx2.BasicAuth(settings.user, settings.password.get_secret_value()),
            headers={"Accept": "application/json", "User-Agent": f"icinga-mcp/{__version__}"},
            verify=verify,
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

    async def list(self, path: str, filters: Sequence[str]) -> list[dict[str, Any]]:
        """GET a list view with pre-built filter expressions (see ``eq``/``compare``/``any_of``)."""
        if not _PATH.fullmatch(path):
            raise ValueError(f"invalid Icinga Web path: {path!r}")
        query = "&".join([*filters, "format=json"])
        try:
            response = await self._http.get(f"{path}?{query}")
        except httpx2.TimeoutException as exc:
            raise IcingaConnectionError(
                f"Icinga Web did not answer within {self._settings.timeout}s; "
                "narrow the time window or filter by host"
            ) from exc
        except httpx2.TransportError as exc:
            raise IcingaConnectionError(
                f"cannot connect to Icinga Web {self._settings.url}: {exc}"
            ) from exc

        if response.status_code == 401:
            raise IcingaAuthError(401, "Icinga Web login failed (check ICINGAWEB_USER/PASSWORD)")
        if response.status_code == 403:
            raise IcingaPermissionError(403, "the Icinga Web user may not access this view")
        if response.status_code == 404:
            raise IcingaNotFoundError(
                404,
                f"{path} not found in Icinga Web (is the module enabled? is ICINGAWEB_URL "
                "the Icinga Web root?)",
            )
        if "json" not in response.headers.get("content-type", ""):
            raise IcingaAPIError(response.status_code, _html_error(response.text))
        try:
            data = response.json()
        except ValueError as exc:
            raise IcingaAPIError(response.status_code, "invalid JSON from Icinga Web") from exc
        if not response.is_success:
            raise IcingaAPIError(response.status_code, str(data)[:300])
        if not isinstance(data, list):
            raise IcingaAPIError(response.status_code, f"unexpected answer: {str(data)[:300]}")
        return data


def _html_error(text: str) -> str:
    plain = " ".join(html.unescape(_TAGS.sub(" ", text)).split())
    if "Allowed memory size" in plain:
        return (
            "Icinga Web ran out of memory for this query; narrow the time window or filter "
            "by host/service"
        )
    return f"Icinga Web returned HTML instead of JSON: {plain[:300]}"
