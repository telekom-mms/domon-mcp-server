from collections.abc import Iterable

from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import ServerSettings
from icinga_mcp.errors import ConfigError
from icinga_mcp.tools import comments, downtimes, objects, problems, status
from icinga_mcp.tools.base import ToolSpec

_MODULES = (status, problems, objects, comments, downtimes)

TOOLSETS: tuple[str, ...] = tuple(module.TOOLSET for module in _MODULES)


def build_all(client: IcingaClient) -> list[ToolSpec]:
    return [spec for module in _MODULES for spec in module.build(client)]


def select_tools(specs: Iterable[ToolSpec], settings: ServerSettings) -> list[ToolSpec]:
    """Apply toolset selection, disabled tools and read-only mode; sorted by name."""
    specs = list(specs)
    known_toolsets = {spec.toolset for spec in specs}
    known_tools = {spec.name for spec in specs}

    unknown_sets = set(settings.toolsets or []) - known_toolsets
    if unknown_sets:
        raise ConfigError(
            f"unknown toolset(s) in ICINGA_MCP_TOOLSETS: {', '.join(sorted(unknown_sets))}; "
            f"available: {', '.join(sorted(known_toolsets))}"
        )
    unknown_tools = set(settings.disabled_tools) - known_tools
    if unknown_tools:
        raise ConfigError(
            f"unknown tool(s) in ICINGA_MCP_DISABLED_TOOLS: {', '.join(sorted(unknown_tools))}"
        )

    enabled_sets = set(settings.toolsets) if settings.toolsets else known_toolsets
    selected = [
        spec
        for spec in specs
        if spec.toolset in enabled_sets
        and spec.name not in settings.disabled_tools
        and not (spec.write and settings.read_only)
    ]
    return sorted(selected, key=lambda spec: spec.name)


__all__ = ["TOOLSETS", "ToolSpec", "build_all", "select_tools"]
