# Icinga test instances (template)

Copy this file to `instances.local.md` (git-ignored) and fill in the real values.
Never put passwords into this file; only describe where they come from.

## <instance name> (default for live tests)

| Setting | Value |
|---|---|
| `ICINGA_URL` | `https://<icinga-master>:5665` |
| `ICINGA_USER` | `<api user>` |
| Password source | e.g. "ask the user", a password manager entry, an environment variable |
| `ICINGA_TLS_SERVER_NAME` | `<certificate CN, only if it differs from the URL host>` |
| CA fingerprint (SHA-256) | `<fingerprint confirmed by the Icinga admins>` |
| Icinga version | |
| Icinga Web 2 URL | |
| History backend | `monitoring` (IDO) or `icingadb` |

Notes: permissions of the ApiUser, reachability, quirks.
