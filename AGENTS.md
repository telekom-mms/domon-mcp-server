# icinga-mcp – guide for coding agents

MCP server that lets an LLM analyse an Icinga 2 monitoring setup: read current alerts and
object details, later acknowledge problems, add comments, schedule downtimes and look at alert
history. Distributed via PyPI (`uvx icinga-mcp`), used by Linux, macOS and Windows users.
User-facing documentation: [README.md](README.md). Code, comments and docs are in English.

## Status and roadmap

| Area | Status |
|---|---|
| Config, Icinga 2 API client, `check` / `list-tools` CLI | done |
| Read tools: `icinga_status`, `list_problems`, `get_host`, `get_service`, `list_hosts`, `list_comments`, `list_downtimes` | done, verified live |
| Write tools: `acknowledge_problem`, `remove_acknowledgement`, `add_comment`, `schedule_downtime`, `remove_downtime` | done; verified live (downtime, comment, ack skip) |
| History via Icinga Web 2 `monitoring` module (`/monitoring/list/eventhistory?format=json`) | planned, needs web login |
| Icinga Director read tools (`/director/...`, `Accept: application/json`) | planned, needs web login |
| Streamable HTTP transport, central deployment | later |

Out of scope: creating, changing or deleting monitoring objects (that is done via Director/Ansible).

## Key decisions (do not change without asking)

- **MCP protocol `2026-07-28`** via the official SDK `mcp>=2.3,<3` (`mcp.server.MCPServer`), which
  also serves `2025-11-25` clients. Transport: stdio. The SDK depends on `httpx2`; use it, not `httpx`.
- **Configuration only via environment variables** (`ICINGA_*`, `ICINGA_MCP_*`, see
  [config.py](src/icinga_mcp/config.py)). Credentials are written into the user's `mcp.json`;
  there is deliberately no OS keyring/keychain integration (cross-platform simplicity).
- **TLS verification is optional**: default off with a warning on stderr; `ICINGA_CA_CERT`
  enables it, `ICINGA_TLS_SERVER_NAME` handles certificates whose CN differs from the URL host.
- **Read-only by default** (`ICINGA_MCP_READ_ONLY=true`). Write tools target explicit object
  names (`host` / `host!service`, never patterns or free filters), resolve all targets first,
  respect `ICINGA_MCP_MAX_TARGETS`, and need the user's confirmation: elicitation via
  `Resolve`/`Elicit` (works on 2026-07-28 via MRTR and on 2025-11-25), otherwise a preview plus
  a second call with `confirm=true`. `ICINGA_MCP_AUTHOR` is required for write tools.
- **The Icinga 2 API has no history.** History comes from Icinga Web 2 (IDO, `monitoring`
  module); keep the history backend replaceable because an IcingaDB migration is planned.

## Architecture

```
src/icinga_mcp/
  cli.py              # `icinga-mcp [serve|check|list-tools]`, serve is the default
  config.py           # IcingaSettings (ICINGA_*), ServerSettings (ICINGA_MCP_*)
  server.py           # create_server(): MCPServer + enabled tools, closes client in lifespan
  clients/icinga_api.py  # async Icinga 2 REST client, error mapping, SNI override
  tools/__init__.py   # registry: _MODULES, select_tools() (toolsets, disabled, read-only)
  tools/base.py       # ToolSpec, icinga_tool (errors -> ToolError), Filter (filter_vars)
  tools/actions.py    # write tool helpers: resolve_targets, approval (elicit/confirm), run_action
  tools/convert.py    # raw Icinga objects -> models, attribute lists per object type
  tools/<toolset>.py  # one module per toolset: TOOLSET + build(client) -> list[ToolSpec]
  models.py           # pydantic result models (become the tools' outputSchema)
  formatting.py       # state names, timestamps (ISO 8601 local time), truncation
  timeparse.py        # 'now' / ISO 8601 times and durations like '2h' for downtimes
  redaction.py        # masks sensitive custom vars (*pass*, *secret*, *token*, *basicauth*)
  permissions.py      # required ApiUser permissions, wildcard matching
```

## Conventions

- **New tool**: add it to the `build()` of its toolset module (or a new module registered in
  `tools/__init__._MODULES`). The docstring is the description the LLM sees: say what it
  returns and when to use it. Parameters use `Annotated[..., Field(description=...)]`; return a
  model from `models.py`. Wrap with `@icinga_tool`. Mark write tools with `ToolSpec(write=True)`.
- **Write tool pattern** (see `tools/acknowledgements.py`): a resolver resolves the targets, a
  second resolver builds the confirmation message and returns `approval(...)`, the tool body
  returns `not_applied(...)` (nothing to do / preview / user said no) or runs the action.
  Confirmation messages must be deterministic (built only from arguments and target names,
  never from `now`), because MRTR re-runs resolvers on every round. Validate input in the
  resolver so errors appear before the user is asked. Resolvers are wrapped with
  `@icinga_tool` too.
- Tool modules must **not** use `from __future__ import annotations` (the SDK builds the input
  schema from real annotation objects). Other modules use it.
- **Never format user input into Icinga filter strings**; use `Filter.add(expr, name=value)`
  so values travel in `filter_vars`.
- Request only attributes that exist on the object type (`HOST_*` / `SERVICE_*` lists in
  `convert.py`): Icinga answers unknown attributes with HTTP 200 and a per-object `code: 400`.
  The client raises on those.
- Pass custom vars and anything config-like through `redact()` before returning it.
- Expected failures (not found, bad input) raise `ToolError` with a hint for the model; Icinga
  client errors are converted automatically by `@icinga_tool`.
- **stdout is the MCP stdio channel**: log only to stderr, never `print()` in server code.
- Keep everything platform-neutral (`pathlib`, no shell calls, no OS-specific branches); CI runs
  on Linux, Windows and macOS.
- Tool lists are sorted by name (deterministic `tools/list`, required by the spec).

## Build and test

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy   # mypy is strict
uv run pytest                    # unit tests, no network
uv run pytest -m integration     # read-only tests against a real Icinga (ICINGA_* env)
uv run icinga-mcp list-tools
```

- Unit tests fake Icinga with `httpx2.MockTransport` (`Recorder` in
  [tests/conftest.py](tests/conftest.py), `FakeIcinga` in
  [tests/test_server.py](tests/test_server.py)) and talk MCP through the in-memory
  `mcp.Client(server)`, for both protocol versions.
- For anything against a real instance (checks, integration tests, smoke tests, reproducing
  bugs with live data) use the skill [icinga-live-test](.github/skills/icinga-live-test/SKILL.md).

## Security

- Never commit, log or print credentials. `.vscode/mcp.json` and `*.local.md` are git-ignored.
- Instance-specific details (internal host names, users) live only in git-ignored
  `*.local.md` files, not in committed files: the repository will become public.
- Live testing is read-only unless the user explicitly approves a write test on a named test object.
