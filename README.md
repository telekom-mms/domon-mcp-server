# icinga-mcp

MCP server for analysing an [Icinga 2](https://icinga.com/) monitoring setup with an LLM.
Implements the [Model Context Protocol](https://modelcontextprotocol.io/) revision `2026-07-28`
(via the official Python SDK, which also falls back to `2025-11-25` for older clients).

> Status: early development. Not yet published on PyPI.

## Scope (v1)

- Read active problems, host/service details, comments and downtimes (Icinga 2 REST API)
- Acknowledge problems, add comments, schedule/remove downtimes (opt-in, every change is
  confirmed by the user)
- Tools are grouped into toolsets that can be enabled/disabled individually; read-only by default
- Alert history via Icinga Web 2 (`monitoring` module), optional
- Planned: Icinga Director (read-only), IcingaDB history backend

Runs on Linux, macOS and Windows (Python 3.11+).

Creating, changing or deleting monitoring objects is intentionally out of scope.

## Installation

icinga-mcp is started by your MCP client via [uv](https://docs.astral.sh/uv/). Install uv once:

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Quick start (VS Code)

Add the server to your MCP configuration. Recommended: the user configuration
(command palette: **MCP: Open User Configuration**), so the credentials stay outside of any
repository. A workspace file `.vscode/mcp.json` works too, but must not be committed.

```json
{
  "servers": {
    "icinga": {
      "type": "stdio",
      "command": "uvx",
      "args": ["icinga-mcp"],
      "env": {
        "ICINGA_URL": "https://icinga-master.example.com:5665",
        "ICINGA_USER": "icinga-mcp",
        "ICINGA_PASSWORD": "change-me",
        "ICINGAWEB_URL": "https://icinga.example.com",
        "ICINGAWEB_USER": "jdoe",
        "ICINGAWEB_PASSWORD": "change-me",
        "ICINGA_MCP_READ_ONLY": "false",
        "ICINGA_MCP_AUTHOR": "jdoe"
      }
    }
  }
}
```

The `ICINGAWEB_*` lines enable the alert history (optional). Omit the last two lines to keep
icinga-mcp read-only. The password is stored in plain text in this file. Use a dedicated, minimally privileged
`ApiUser` (see [permissions](#icinga-2-api-permissions)) and protect the file accordingly.

Until icinga-mcp is published on PyPI, run it from a local checkout instead of `uvx`:

```json
      "command": "uv",
      "args": ["run", "--directory", "/path/to/icinga-mcp", "icinga-mcp"],
```

On Windows use forward slashes or escaped backslashes in JSON paths, e.g.
`"C:/Users/me/icinga-mcp"`.

## Configuration

All settings are read from environment variables (the `env` block of the MCP configuration).

| Variable | Required | Description |
|---|---|---|
| `ICINGA_URL` | yes | Icinga 2 API base URL, e.g. `https://icinga-master:5665` |
| `ICINGA_USER` | yes | Icinga 2 `ApiUser` name |
| `ICINGA_PASSWORD` | yes | API password |
| `ICINGA_CA_CERT` | no | Path to the CA that signed the API certificate (e.g. the Icinga CA). Setting it enables TLS certificate verification |
| `ICINGA_TLS_SERVER_NAME` | no | Expected certificate host name if it differs from the URL host (used for SNI and verification), e.g. when the API sits behind a Kubernetes ingress |
| `ICINGA_VERIFY_TLS` | no | Force verification on (`true`, e.g. for publicly trusted certificates without `ICINGA_CA_CERT`) or off (`false`). Default: on if `ICINGA_CA_CERT` is set, otherwise off |
| `ICINGA_TIMEOUT` | no | Request timeout in seconds (default `30`) |

### Alert history (optional)

The Icinga 2 API has no history. `get_history` reads it from Icinga Web 2 (IDO database via
the `monitoring` module) and is only available when these variables are set:

| Variable | Required | Description |
|---|---|---|
| `ICINGAWEB_URL` | yes | Icinga Web 2 root URL, e.g. `https://icinga.example.com` (or `.../icingaweb2`) |
| `ICINGAWEB_USER` | yes | Icinga Web user (needs access to the monitoring module) |
| `ICINGAWEB_PASSWORD` | yes | Password (HTTP basic auth) |
| `ICINGAWEB_CA_CERT` | no | CA bundle if the web server certificate is not publicly trusted |
| `ICINGAWEB_VERIFY_TLS` | no | Default `true`; set `false` only for debugging |
| `ICINGAWEB_TIMEOUT` | no | Request timeout in seconds (default `60`) |
| `ICINGA_MCP_MAX_HISTORY` | no | Longest time window per query (default `31d`) |

### TLS certificate verification (optional)

Icinga 2 API certificates are usually signed by the internal Icinga CA, which no operating
system trusts. By default icinga-mcp therefore connects **without certificate verification**
and logs a warning. The API password is still sent encrypted, but the server identity is not
checked, so a man-in-the-middle could capture the credentials.

To enable verification, store the CA once per instance and add to the `env` block:

```json
        "ICINGA_CA_CERT": "/home/me/.config/icinga-mcp/<instance>-ca.pem",
        "ICINGA_TLS_SERVER_NAME": "<CN of the server certificate>"
```

`ICINGA_TLS_SERVER_NAME` is only needed if the certificate name differs from the URL host.
The CA is the last certificate printed by the following command (OpenSSL is included in
Linux, macOS and Git for Windows):

```bash
openssl s_client -connect <host>:5665 -showcerts </dev/null
```

Compare the CA fingerprint (`openssl x509 -in <file> -noout -fingerprint -sha256`) with the
one provided by your Icinga administrators.

### Icinga 2 API permissions

Minimal `ApiUser` permissions:

```
object ApiUser "icinga-mcp" {
  password = "..."
  permissions = [
    "status/query",
    "objects/query/Host", "objects/query/Service",
    "objects/query/HostGroup", "objects/query/ServiceGroup",
    "objects/query/Comment", "objects/query/Downtime",
    // only needed for write tools
    "actions/acknowledge-problem", "actions/remove-acknowledgement",
    "actions/add-comment", "actions/schedule-downtime", "actions/remove-downtime",
  ]
}
```

## Tools

| Tool | Toolset | Mode | Description |
|---|---|---|---|
| `icinga_status` | `status` | read | Version, feature flags, host/service counts per state, unhandled problems |
| `list_problems` | `problems` | read | Current alerts (DOWN hosts, WARNING/CRITICAL/UNKNOWN services); filters for host/service patterns, host group, states, handled, hard states |
| `get_host` | `objects` | read | Host details incl. check output, perfdata, custom vars (redacted) and its services |
| `get_service` | `objects` | read | Service details incl. check output, perfdata, command and custom vars (redacted) |
| `list_hosts` | `objects` | read | Search hosts by name/address pattern or host group |
| `acknowledge_problem` | `acknowledgements` | write | Acknowledge problems (sticky, notify, expiry); OK or already acknowledged objects are skipped |
| `remove_acknowledgement` | `acknowledgements` | write | Remove acknowledgements |
| `list_comments` | `comments` | read | Comments and acknowledgement texts, newest first |
| `add_comment` | `comments` | write | Add a comment (note) to hosts/services |
| `list_downtimes` | `downtimes` | read | Scheduled downtimes and whether they are in effect |
| `get_history` | `history` | read | Alert history in a time window (`since` = `24h`, `7d` or ISO 8601): hard/soft state changes, notifications, acknowledgements, comments, downtimes, flapping; summary with the most active objects. Needs Icinga Web |
| `schedule_downtime` | `downtimes` | write | Fixed or flexible downtime (`start` = `now` or ISO 8601, `end` or `duration` like `2h`), optionally for all services of a host |
| `remove_downtime` | `downtimes` | write | Remove downtimes by name (from `list_downtimes`) |

Custom variables whose names look sensitive (`*pass*`, `*secret*`, `*token*`, `*basicauth*`, ...)
are replaced by `***REDACTED***` in all tool results.

### Enabling and disabling tools

| Variable | Default | Description |
|---|---|---|
| `ICINGA_MCP_TOOLSETS` | all | Comma-separated toolsets to enable, e.g. `status,problems,objects` |
| `ICINGA_MCP_DISABLED_TOOLS` | none | Comma-separated tool names to hide, e.g. `remove_downtime` |
| `ICINGA_MCP_READ_ONLY` | `true` | Hide all write tools. Set to `false` to enable them |

Disabled tools are not registered at all, so the model never sees them.
`icinga-mcp list-tools` shows the effective selection.

### Write tools and safety

Write tools are only available with `ICINGA_MCP_READ_ONLY=false` and need these settings:

| Variable | Default | Description |
|---|---|---|
| `ICINGA_MCP_AUTHOR` | (required) | Name recorded as author in Icinga, e.g. your user name |
| `ICINGA_MCP_MAX_TARGETS` | `20` | Maximum number of objects per call |
| `ICINGA_MCP_MAX_DOWNTIME` | `14d` | Maximum length of a downtime |
| `ICINGA_MCP_REQUIRE_ELICITATION` | `false` | Refuse changes from clients that cannot ask the user (see below) |

Safety measures:

- Targets are exact object names (`host` or `host!service`), never patterns or free filters.
  All targets are looked up first; unknown names abort the call before anything is changed.
- **Every change is confirmed by the user.** If the MCP client supports
  [elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation)
  (e.g. VS Code), the user gets a dialog listing the objects and options. Clients without
  elicitation get a preview on the first call; only a second call with `confirm=true` applies
  the change. In that mode the model is instructed to ask the user first, but technically it
  could set `confirm=true` on its own; set `ICINGA_MCP_REQUIRE_ELICITATION=true` to rule that out.
- Results report per object whether Icinga applied the change.

## Command line

| Command | Description |
|---|---|
| `icinga-mcp` / `icinga-mcp serve` | Run the MCP server on stdio (started by the MCP client) |
| `icinga-mcp check` | Test connection, TLS, credentials, API permissions and (if configured) Icinga Web history access |
| `icinga-mcp list-tools` | Show all tools and which ones are enabled (no credentials needed) |

`check` reads the same environment variables as the server:

```bash
# Linux / macOS
ICINGA_URL=https://icinga-master:5665 ICINGA_USER=icinga-mcp ICINGA_PASSWORD='...' uvx icinga-mcp check
```

```powershell
# Windows (PowerShell)
$env:ICINGA_URL = "https://icinga-master:5665"; $env:ICINGA_USER = "icinga-mcp"; $env:ICINGA_PASSWORD = "..."
uvx icinga-mcp check
```

## Development

```bash
uv sync
uv run pytest                    # unit tests
uv run pytest -m integration     # read-only tests against a real instance (needs ICINGA_* env)
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

See [AGENTS.md](AGENTS.md) for architecture and conventions.
