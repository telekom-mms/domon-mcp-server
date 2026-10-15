---
name: icinga-live-test
description: 'Run icinga-mcp against a real Icinga 2 instance in a reproducible, read-only way. Use when: verifying changes live, running integration tests (pytest -m integration), smoke-testing all MCP tools over stdio, checking connection/TLS/permissions with icinga-mcp check, inspecting real Icinga API attributes, or reproducing a bug with live monitoring data.'
---

# Live testing against a real Icinga 2 instance

## Rules

- **Read-only.** Never call write tools or `/v1/actions/*` unless the user explicitly approves a
  write test on a named test object; clean up afterwards (remove ack / downtime / comment).
- Never print, log or commit the password. Do not paste raw API output containing custom vars
  without redaction.
- Live data changes constantly (problem counts, even host counts after Director deployments).
  Assert on structure and invariants, not on exact numbers.

## Procedure

1. **Instance details**: read [instances.local.md](./instances.local.md) (git-ignored). If it
   does not exist, copy [instances.example.md](./instances.example.md) to `instances.local.md`
   and ask the user for URL, user, optional TLS settings and where the password comes from.
2. **Environment**: set `ICINGA_URL`, `ICINGA_USER`, `ICINGA_PASSWORD` (plus `ICINGA_CA_CERT` /
   `ICINGA_TLS_SERVER_NAME` if verification is wanted) for the commands only, without echoing
   the password. Bash: `ICINGA_PASSWORD="$(<command>)" uv run ...`;
   PowerShell: `$env:ICINGA_PASSWORD = <command>; uv run ...`.
3. **Connectivity and permissions**: `uv run icinga-mcp check` (exit 0 = read tools usable;
   the output lists missing and unneeded permissions).
4. **Integration tests**: `uv run pytest -m integration -q`.
5. **End-to-end smoke test over stdio** (spawns the server like an MCP client does and calls
   every enabled read tool): `uv run python .github/skills/icinga-live-test/scripts/smoke_test.py`.
   Optional arguments: `--host <name>` to focus `get_host` / `get_service` on one host.
6. **Write tools without changing anything**: start the server with
   `ICINGA_MCP_READ_ONLY=false ICINGA_MCP_AUTHOR=<name>` and call write tools *without*
   `confirm` from a client without elicitation callback. Icinga is only queried; the result is
   a preview (`applied: false`). Compare comment/downtime/ack counts before and after.
7. Report results compactly (counts, tool errors, protocol version) and update
   `instances.local.md` if you learned something new about the instance (e.g. permissions,
   attribute quirks).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Timeout on port 5665 | Firewall; the API may only be reachable under an internal host name |
| `Hostname mismatch` with `ICINGA_CA_CERT` set | Set `ICINGA_TLS_SERVER_NAME` to the certificate CN |
| Connection closed without message while `ICINGA_TLS_SERVER_NAME` is set | SNI-routing proxy does not know that name |
| `Icinga API error 400: Invalid field specified` | Attribute not valid for that object type (see `convert.py`) |
| 403 `Missing permission` | ApiUser lacks the permission; `icinga-mcp check` lists what is missing |
