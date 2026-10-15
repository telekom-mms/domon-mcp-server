from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from typing import TextIO, cast

import anyio
import httpx2

from icinga_mcp import __version__
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import (
    IcingaSettings,
    ServerSettings,
    load_icinga_settings,
    load_server_settings,
)
from icinga_mcp.errors import ConfigError, IcingaMCPError
from icinga_mcp.permissions import READ_PERMISSIONS, WRITE_PERMISSIONS, missing, unused
from icinga_mcp.server import create_server
from icinga_mcp.tools import TOOLSETS, build_all, select_tools

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_CONNECTION = 2


async def run_check(
    settings: IcingaSettings,
    out: TextIO,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> int:
    """Verify connectivity, TLS, credentials and permissions against the Icinga 2 API."""
    async with IcingaClient(settings, transport=transport) as client:
        status = await client.status("IcingaApplication")
        granted = await client.permissions()

    app = status[0].get("status", {}).get("icingaapplication", {}).get("app", {}) if status else {}
    if not settings.tls_verification_enabled:
        tls = "NOT verified (set ICINGA_CA_CERT to enable certificate verification)"
    else:
        tls = f"verified with {settings.ca_cert or 'system trust store'}"
        if settings.tls_server_name:
            tls += f", server name {settings.tls_server_name}"

    print(f"Icinga 2 API : {settings.url} as {settings.user}", file=out)
    print(f"TLS          : {tls}", file=out)
    print(f"Version      : {app.get('version', 'unknown')}", file=out)
    print(f"Node         : {app.get('node_name', 'unknown')}", file=out)

    missing_read = missing(READ_PERMISSIONS, granted)
    missing_write = missing(WRITE_PERMISSIONS, granted)
    extra = unused(granted, READ_PERMISSIONS + WRITE_PERMISSIONS)
    print(
        f"Read tools   : {'OK' if not missing_read else 'missing ' + ', '.join(missing_read)}",
        file=out,
    )
    print(
        f"Write tools  : {'OK' if not missing_write else 'missing ' + ', '.join(missing_write)}",
        file=out,
    )
    if extra:
        print(f"Not needed   : {', '.join(extra)}", file=out)
    return EXIT_OK if not missing_read else EXIT_CONNECTION


def list_tools(settings: ServerSettings, out: TextIO) -> int:
    """Print every tool and whether the current configuration enables it."""
    # The tool functions are never called here, so no client (and no credentials) is needed.
    specs = build_all(cast(IcingaClient, None))
    enabled = {spec.name for spec in select_tools(specs, settings)}
    print(f"{'TOOL':<18} {'TOOLSET':<11} {'MODE':<6} ENABLED", file=out)
    for spec in sorted(specs, key=lambda s: (TOOLSETS.index(s.toolset), s.name)):
        mode = "write" if spec.write else "read"
        print(
            f"{spec.name:<18} {spec.toolset:<11} {mode:<6} "
            f"{'yes' if spec.name in enabled else 'no'}",
            file=out,
        )
    return EXIT_OK


def serve(icinga_settings: IcingaSettings, server_settings: ServerSettings) -> int:
    server = create_server(icinga_settings, server_settings)
    server.run("stdio")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="icinga-mcp", description="MCP server for Icinga 2")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server on stdio (default)")
    sub.add_parser("check", help="test connection, TLS, credentials and API permissions")
    sub.add_parser("list-tools", help="show all tools and which ones are enabled")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "serve"
    logging.basicConfig(
        stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    try:
        server_settings = load_server_settings()
        if command == "list-tools":
            return list_tools(server_settings, sys.stdout)
        icinga_settings = load_icinga_settings()
        if command == "serve":
            return serve(icinga_settings, server_settings)

        async def _check() -> int:
            return await run_check(icinga_settings, sys.stdout)

        return anyio.run(_check)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except IcingaMCPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONNECTION
