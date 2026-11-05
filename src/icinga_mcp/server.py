from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server import MCPServer

from icinga_mcp import __version__
from icinga_mcp.clients.director import DirectorClient
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.clients.icingaweb import IcingaWebClient
from icinga_mcp.config import IcingaSettings, IcingaWebSettings, ServerSettings
from icinga_mcp.history import build_history_backend
from icinga_mcp.tools import build_all, check_write_config, select_tools

INSTRUCTIONS = """\
Access to an Icinga 2 monitoring system for analysing alerts.

Suggested workflow: icinga_status for an overview, list_problems for current alerts
(only unhandled ones by default), get_host / get_service for details and plugin output,
list_comments and list_downtimes for context, get_history (if available) for what happened
over time, director_list / director_get (if available) for how checks are configured
(templates, apply rules, commands).

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
    web_settings: IcingaWebSettings | None = None,
    client: IcingaClient | None = None,
    web_client: IcingaWebClient | None = None,
) -> MCPServer:
    """Build the MCP server with the tools enabled by ``server_settings``.

    History and Director tools are only available when Icinga Web is configured.
    """
    icinga = client or IcingaClient(icinga_settings)
    web = web_client or (IcingaWebClient(web_settings) if web_settings else None)
    specs = select_tools(
        build_all(
            icinga,
            server_settings,
            history_backend=build_history_backend(
                web, web_settings.history_backend if web_settings else "icingadb"
            )
            if web
            else None,
            director_client=DirectorClient(web) if web else None,
        ),
        server_settings,
    )
    check_write_config(specs, server_settings)

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await icinga.aclose()
            if web:
                await web.aclose()

    server: MCPServer = MCPServer(
        name="icinga-mcp",
        title="Icinga 2",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://github.com/telekom-mms/domon-mcp-server",
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
