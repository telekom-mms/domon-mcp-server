"""Tool infrastructure shared by all toolsets.

Tool modules deliberately do not use ``from __future__ import annotations``: the SDK
derives the input schema from the real annotation objects of the wrapped function.
"""

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ParamSpec, TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from icinga_mcp.errors import IcingaMCPError

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True)
class ToolSpec:
    fn: Callable[..., Awaitable[Any]]
    name: str
    toolset: str
    title: str
    write: bool = False
    idempotent: bool = True

    @property
    def annotations(self) -> ToolAnnotations:
        return ToolAnnotations(
            title=self.title,
            read_only_hint=not self.write,
            destructive_hint=False if self.write else None,
            idempotent_hint=self.idempotent,
            open_world_hint=False,
        )

    @property
    def description(self) -> str:
        return (self.fn.__doc__ or "").strip()


def icinga_tool(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Turn Icinga client errors into ``ToolError`` so the model sees a readable message."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except IcingaMCPError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


@dataclass
class Filter:
    """Builds an Icinga filter expression; user input only ever goes into ``vars``."""

    parts: list[str] = field(default_factory=list)
    vars: dict[str, Any] = field(default_factory=dict)

    def add(self, expression: str, **filter_vars: Any) -> None:
        self.parts.append(expression)
        self.vars.update(filter_vars)

    @property
    def expression(self) -> str | None:
        return " && ".join(f"({part})" for part in self.parts) or None
