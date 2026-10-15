from collections.abc import Iterable

from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import ServerSettings
from icinga_mcp.errors import ConfigError
from icinga_mcp.history import HistoryBackend
from icinga_mcp.tools import (
    acknowledgements,
    comments,
    downtimes,
    history,
    objects,
    problems,
    status,
)
from icinga_mcp.tools.base import ToolSpec

_MODULES = (status, problems, objects, acknowledgements, comments, downtimes)

TOOLSETS: tuple[str, ...] = (*(module.TOOLSET for module in _MODULES), history.TOOLSET)

# Toolsets that need an optional data source; value = how to enable it.
_REQUIREMENTS = {history.TOOLSET: "ICINGAWEB_URL, ICINGAWEB_USER and ICINGAWEB_PASSWORD"}


def build_all(
    client: IcingaClient, settings: ServerSettings, history_backend: HistoryBackend | None
) -> list[ToolSpec]:
    specs = [spec for module in _MODULES for spec in module.build(client, settings)]
    if history_backend is not None:
        specs += history.build(history_backend, settings)
    return specs


def select_tools(specs: Iterable[ToolSpec], settings: ServerSettings) -> list[ToolSpec]:
    """Apply toolset selection, disabled tools and read-only mode; sorted by name."""
    specs = list(specs)
    built_toolsets = {spec.toolset for spec in specs}
    known_tools = {spec.name for spec in specs}

    requested = set(settings.toolsets or [])
    unknown_sets = requested - set(TOOLSETS)
    if unknown_sets:
        raise ConfigError(
            f"unknown toolset(s) in ICINGA_MCP_TOOLSETS: {', '.join(sorted(unknown_sets))}; "
            f"available: {', '.join(TOOLSETS)}"
        )
    for name in sorted(requested - built_toolsets):
        raise ConfigError(f"toolset '{name}' is enabled but needs {_REQUIREMENTS[name]}")
    unknown_tools = set(settings.disabled_tools) - known_tools
    if unknown_tools:
        raise ConfigError(
            f"unknown tool(s) in ICINGA_MCP_DISABLED_TOOLS: {', '.join(sorted(unknown_tools))}"
        )

    enabled_sets = requested or built_toolsets
    selected = [
        spec
        for spec in specs
        if spec.toolset in enabled_sets
        and spec.name not in settings.disabled_tools
        and not (spec.write and settings.read_only)
    ]
    return sorted(selected, key=lambda spec: spec.name)


def check_write_config(selected: Iterable[ToolSpec], settings: ServerSettings) -> None:
    if any(spec.write for spec in selected) and not settings.author:
        raise ConfigError(
            "write tools are enabled (ICINGA_MCP_READ_ONLY=false) but ICINGA_MCP_AUTHOR is not "
            "set; it is recorded as author of acknowledgements, comments and downtimes"
        )


__all__ = ["TOOLSETS", "ToolSpec", "build_all", "check_write_config", "select_tools"]
