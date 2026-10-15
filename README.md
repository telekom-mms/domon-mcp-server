# icinga-mcp

MCP server for analysing an [Icinga 2](https://icinga.com/) monitoring setup with an LLM.
Implements the [Model Context Protocol](https://modelcontextprotocol.io/) revision `2026-07-28`
(via the official Python SDK, which also falls back to `2025-11-25` for older clients).

> Status: early development. Not yet published on PyPI.

## Scope (v1)

- Read active problems, host/service details, comments and downtimes (Icinga 2 REST API)
- Tools are grouped into toolsets that can be enabled/disabled individually; read-only by default
- Planned: acknowledge problems, add comments, schedule/remove downtimes (opt-in, with safety
  guards); alert history (Icinga Web 2 `monitoring` module); Icinga Director (read-only)

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
        "ICINGA_PASSWORD": "change-me"
      }
    }
  }
}
```

The password is stored in plain text in this file. Use a dedicated, minimally privileged
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

| Tool | Toolset | Description |
|---|---|---|
| `icinga_status` | `status` | Version, feature flags, host/service counts per state, unhandled problems |
| `list_problems` | `problems` | Current alerts (DOWN hosts, WARNING/CRITICAL/UNKNOWN services); filters for host/service patterns, host group, states, handled, hard states |
| `get_host` | `objects` | Host details incl. check output, perfdata, custom vars (redacted) and its services |
| `get_service` | `objects` | Service details incl. check output, perfdata, command and custom vars (redacted) |
| `list_hosts` | `objects` | Search hosts by name/address pattern or host group |
| `list_comments` | `comments` | Comments and acknowledgement texts, newest first |
| `list_downtimes` | `downtimes` | Scheduled downtimes and whether they are in effect |

Custom variables whose names look sensitive (`*pass*`, `*secret*`, `*token*`, `*basicauth*`, ...)
are replaced by `***REDACTED***` in all tool results.

### Enabling and disabling tools

| Variable | Default | Description |
|---|---|---|
| `ICINGA_MCP_TOOLSETS` | all | Comma-separated toolsets to enable, e.g. `status,problems,objects` |
| `ICINGA_MCP_DISABLED_TOOLS` | none | Comma-separated tool names to hide, e.g. `list_hosts` |
| `ICINGA_MCP_READ_ONLY` | `true` | Hide all tools that change Icinga (write tools follow in a later release) |

Disabled tools are not registered at all, so the model never sees them.
`icinga-mcp list-tools` shows the effective selection.

## Command line

| Command | Description |
|---|---|
| `icinga-mcp` / `icinga-mcp serve` | Run the MCP server on stdio (started by the MCP client) |
| `icinga-mcp check` | Test connection, TLS, credentials and API permissions |
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
