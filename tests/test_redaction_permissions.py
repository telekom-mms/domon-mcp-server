from __future__ import annotations

from typing import Any

from icinga_mcp.permissions import is_granted, missing, unused
from icinga_mcp.redaction import REDACTED, redact


def test_redacts_nested_sensitive_keys() -> None:
    data: dict[str, Any] = {
        "vars": {
            "mail_basic_auth": {"user": "x", "password": "y"},
            "prometheus_basicauth": "user:pw",
            "api_token": "t",
            "db_pass": "p",
            "client_secret": "s",
            "location": "dresden",
        },
        "enable_passive_checks": True,
        "use_password": True,
        "items": [{"apikey": "k", "name": "n"}],
    }

    result = redact(data)

    assert result["vars"]["mail_basic_auth"] == {"user": REDACTED, "password": REDACTED}
    assert result["vars"]["prometheus_basicauth"] == REDACTED
    assert result["vars"]["api_token"] == REDACTED
    assert result["vars"]["db_pass"] == REDACTED
    assert result["vars"]["client_secret"] == REDACTED
    assert result["vars"]["location"] == "dresden"
    assert result["enable_passive_checks"] is True
    assert result["use_password"] is True
    assert result["items"] == [{"apikey": REDACTED, "name": "n"}]
    assert data["vars"]["db_pass"] == "p"


def test_permission_wildcards() -> None:
    granted = ["objects/query/*", "actions/add-comment", "events/*"]
    assert is_granted("objects/query/Host", granted)
    assert not is_granted("actions/schedule-downtime", granted)
    assert is_granted("anything", ["*"])
    assert missing(["objects/query/Host", "status/query"], granted) == ["status/query"]
    assert unused(granted, ["objects/query/Host", "actions/add-comment"]) == ["events/*"]


def test_redaction_keeps_macros_and_flags() -> None:
    command = {
        "arguments": {
            "--password": {
                "value": "$db_password$",
                "description": "DB password",
                "required": True,
            },
            "--authpassphrase": "$snmp_v3_auth$ $snmp_v3_priv$",
            "--pass": "literal-secret",
            "--host": "$address$",
        },
        "vars": {"snmp_v3_use_privpass": False, "api_token": 12345, "token_file": ""},
    }

    result = redact(command)

    assert result["arguments"]["--password"] == {
        "value": "$db_password$",
        "description": REDACTED,
        "required": True,
    }
    assert result["arguments"]["--authpassphrase"] == "$snmp_v3_auth$ $snmp_v3_priv$"
    assert result["arguments"]["--pass"] == REDACTED
    assert result["arguments"]["--host"] == "$address$"
    assert result["vars"] == {
        "snmp_v3_use_privpass": False,
        "api_token": REDACTED,
        "token_file": "",
    }
