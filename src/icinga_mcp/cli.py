from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from typing import TextIO, cast

import anyio
import httpx2

from icinga_mcp import __version__
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.clients.icingaweb import IcingaWebClient
from icinga_mcp.config import (
    IcingaSettings,
    IcingaWebSettings,
    ServerSettings,
    load_icinga_settings,
    load_icingaweb_settings,
    load_server_settings,
)
from icinga_mcp.errors import ConfigError, IcingaMCPError
from icinga_mcp.history import HistoryBackend, MonitoringHistory
from icinga_mcp.permissions import READ_PERMISSIONS, WRITE_PERMISSIONS, missing, unused
from icinga_mcp.server import create_server
from icinga_mcp.tools import TOOLSETS, build_all, check_write_config, select_tools

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_CONNECTION = 2


async def run_check(
    settings: IcingaSettings,
    out: TextIO,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
    web_settings: IcingaWebSettings | None = None,
    web_transport: httpx2.AsyncBaseTransport | None = None,
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
    web_ok = await _check_web(web_settings, out, web_transport)
    return EXIT_OK if not missing_read and web_ok else EXIT_CONNECTION


async def _check_web(
    settings: IcingaWebSettings | None,
    out: TextIO,
    transport: httpx2.AsyncBaseTransport | None,
) -> bool:
    if settings is None:
        print("Icinga Web   : not configured (history tools disabled)", file=out)
        return True
    print(f"Icinga Web   : {settings.url} as {settings.user}", file=out)
    now = time.time()
    try:
        async with IcingaWebClient(settings, transport=transport) as web:
            events = await MonitoringHistory(web).events(
                start=now - 3600, end=now, host=None, service=None, categories=["state"]
            )
    except IcingaMCPError as exc:
        print(f"History      : ERROR {exc}", file=out)
        return False
    print(
        f"History      : OK (monitoring module, {len(events)} state changes in the last hour)",
        file=out,
    )
    return True


def list_tools(settings: ServerSettings, web_configured: bool, out: TextIO) -> int:
    """Print every tool and whether the current configuration enables it."""
    # The tool functions are never called here, so no clients (and no credentials) are needed.
    client = cast(IcingaClient, None)
    placeholder = cast(HistoryBackend, object())
    specs = build_all(client, settings, placeholder)
    selected = select_tools(
        build_all(client, settings, placeholder if web_configured else None), settings
    )
    try:
        check_write_config(selected, settings)
    except ConfigError as exc:
        print(f"warning: {exc}", file=sys.stderr)
    enabled = {spec.name for spec in selected}
    print(f"{'TOOL':<23} {'TOOLSET':<17} {'MODE':<6} ENABLED", file=out)
    for spec in sorted(specs, key=lambda s: (TOOLSETS.index(s.toolset), s.name)):
        mode = "write" if spec.write else "read"
        state = "yes" if spec.name in enabled else "no"
        if spec.toolset == "history" and not web_configured:
            state = "no (needs ICINGAWEB_URL)"
        print(f"{spec.name:<23} {spec.toolset:<17} {mode:<6} {state}", file=out)
    return EXIT_OK


def serve(
    icinga_settings: IcingaSettings,
    server_settings: ServerSettings,
    web_settings: IcingaWebSettings | None,
) -> int:
    server = create_server(icinga_settings, server_settings, web_settings=web_settings)
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
        web_settings = load_icingaweb_settings()
        if command == "list-tools":
            return list_tools(server_settings, web_settings is not None, sys.stdout)
        icinga_settings = load_icinga_settings()
        if command == "serve":
            return serve(icinga_settings, server_settings, web_settings)

        async def _check() -> int:
            return await run_check(icinga_settings, sys.stdout, web_settings=web_settings)

        return anyio.run(_check)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except IcingaMCPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONNECTION
