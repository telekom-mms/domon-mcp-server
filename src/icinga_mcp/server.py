from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server import MCPServer

from icinga_mcp import __version__
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import IcingaSettings, ServerSettings
from icinga_mcp.tools import build_all, check_write_config, select_tools

INSTRUCTIONS = """\
Access to an Icinga 2 monitoring system for analysing alerts.

Suggested workflow: icinga_status for an overview, list_problems for current alerts
(only unhandled ones by default), get_host / get_service for details and plugin output,
list_comments and list_downtimes for context.

Semantics:
- Host states: UP, DOWN. Service states: OK, WARNING, CRITICAL, UNKNOWN.
- "handled" = acknowledged or in a downtime. Unhandled problems need attention.
- "soft" states are not yet confirmed (the check is being retried), "hard" states are.
- Times are ISO 8601 with UTC offset; "duration" is the time since the last state change.
- Sensitive custom variables (passwords, tokens, ...) are redacted.

Changing tools (acknowledge, comment, downtime) target exact object names ('host' or
'host!service') and always need the user's confirmation. Never set confirm=true on your own:
only after the user explicitly approved the preview shown to them.
"""


def create_server(
    icinga_settings: IcingaSettings,
    server_settings: ServerSettings,
    *,
    client: IcingaClient | None = None,
) -> MCPServer:
    """Build the MCP server with the tools enabled by ``server_settings``."""
    icinga = client or IcingaClient(icinga_settings)
    specs = select_tools(build_all(icinga, server_settings), server_settings)
    check_write_config(specs, server_settings)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await icinga.aclose()

    server: MCPServer = MCPServer(
        name="icinga-mcp",
        title="Icinga 2",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://github.com/hektor1966/icinga-mcp",
        lifespan=lifespan,
    )
    for spec in specs:
        server.add_tool(
            spec.fn,
            name=spec.name,
            title=spec.title,
            description=spec.description,
            annotations=spec.annotations,
            structured_output=True,
        )
    return server
