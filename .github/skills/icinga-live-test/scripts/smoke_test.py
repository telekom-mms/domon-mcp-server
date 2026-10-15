"""Smoke test: start icinga-mcp over stdio like an MCP client and call every read tool.

Usage (from the repository root, with ICINGA_* set in the environment):

    uv run python .github/skills/icinga-live-test/scripts/smoke_test.py [--host NAME]

Read-only. Prints a compact summary per tool and exits non-zero if any tool call fails.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import anyio
from mcp import Client, StdioServerParameters

READ_TOOLS = (
    "director_get",
    "director_list",
    "get_history",
    "icinga_status",
    "list_problems",
    "list_hosts",
    "get_host",
    "get_service",
    "list_comments",
    "list_downtimes",
)


async def run(host: str | None) -> int:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "icinga_mcp", "serve"], env=dict(os.environ)
    )
    failures = 0
    async with Client(params) as client:
        info = client.server_info
        print(
            f"protocol {client.protocol_version}, server {info.name if info else '?'} "
            f"{info.version if info else ''}"
        )
        tools = {t.name for t in (await client.list_tools()).tools}
        print(f"tools: {', '.join(sorted(tools))}")

        async def call(name: str, args: dict[str, Any] | None = None) -> Any:
            nonlocal failures
            if name not in tools:
                print(f"- {name}: disabled")
                return None
            result = await client.call_tool(name, args or {})
            if result.is_error:
                failures += 1
                text = result.content[0].text if result.content else ""  # type: ignore[union-attr]
                print(f"- {name}: ERROR {text}")
                return None
            return result.structured_content

        status = await call("icinga_status")
        if status:
            print(
                f"- icinga_status: {status['version']}, hosts {status['hosts']['by_state']}, "
                f"services {status['services']['by_state']}"
            )

        problems = await call("list_problems", {"handled": "all", "limit": 5})
        if problems:
            print(f"- list_problems: {problems['summary']}")

        hosts = await call("list_hosts", {"pattern": host, "limit": 5} if host else {"limit": 5})
        target = host or (hosts["hosts"][0]["name"] if hosts and hosts["hosts"] else None)
        if hosts:
            print(f"- list_hosts: {hosts['total']} total")

        if target:
            details = await call("get_host", {"name": target})
            if details:
                redacted = [k for k, v in details["vars"].items() if v == "***REDACTED***"]
                print(
                    f"- get_host {target}: {details['state']}, services "
                    f"{details['service_summary']}, redacted vars {redacted}"
                )
                if details["services"]:
                    service = details["services"][0]["name"]
                    svc = await call("get_service", {"host": target, "service": service})
                    if svc:
                        print(f"- get_service {service}: {svc['state']} {svc['check_attempt']}")

        history = await call("get_history", {"since": "24h", "limit": 5})
        if history:
            print(f"- get_history: {history['summary']['by_event']}")

        if target:
            director_host = await call("director_get", {"object_type": "host", "name": target})
            if director_host:
                print(
                    f"- director_get {target}: imports {director_host['properties'].get('imports')}"
                )
        rules = await call("director_list", {"object_type": "service_apply_rule", "limit": 1})
        if rules:
            print(f"- director_list service_apply_rule: {rules['total']} total")

        comments = await call("list_comments", {"limit": 5})
        if comments:
            print(f"- list_comments: {comments['total']} total")
        downtimes = await call("list_downtimes", {"limit": 5})
        if downtimes:
            print(f"- list_downtimes: {downtimes['total']} total")

        missing = await client.call_tool("get_host", {"name": "icinga-mcp-smoke-test-missing"})
        if "get_host" in tools and not missing.is_error:
            failures += 1
            print("- get_host for a missing host did not return an error")

    print("OK" if not failures else f"{failures} failure(s)")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", help="host to use for get_host / get_service")
    args = parser.parse_args()
    return anyio.run(run, args.host)


if __name__ == "__main__":
    raise SystemExit(main())
