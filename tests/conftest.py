from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from icinga_mcp.config import IcingaSettings

Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> IcingaSettings:
    return IcingaSettings(url="https://icinga.example:5665", user="mcp", password="s3cret")


def json_response(status: int, body: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(
        status, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )


class Recorder:
    """MockTransport handler that records requests and answers from a route table."""

    def __init__(self, routes: dict[tuple[str, str], httpx2.Response]) -> None:
        self.routes = routes
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key not in self.routes:
            return json_response(404, {"error": 404, "status": f"no route for {key}"})
        return self.routes[key]

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self)
