from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

# "pass" must not hit Icinga attributes such as enable_passive_checks.
_SENSITIVE_KEY = re.compile(
    r"(?<!by)pass(?!ive)|secret|token|basic_?auth|api_?key|private_?key|credential",
    re.IGNORECASE,
)
# Values that only reference Icinga macros (e.g. "$snmp_password$") contain no secret.
_ONLY_MACROS = re.compile(r"^\s*(\$[A-Za-z0-9_.]+\$\s*)+$")


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(key))


def redact(data: Any, *, sensitive: bool = False) -> Any:
    """Return a copy of ``data`` with values below sensitive-looking keys masked.

    Everything below a sensitive key is masked except booleans, ``None``, empty strings and
    pure macro references, so flags like ``use_password: true`` and command arguments like
    ``"--password": {"value": "$db_password$"}`` stay readable.
    """
    if isinstance(data, dict):
        return {
            key: redact(
                value, sensitive=sensitive or (isinstance(key, str) and is_sensitive_key(key))
            )
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact(item, sensitive=sensitive) for item in data]
    if not sensitive or data is None or isinstance(data, bool) or data == "":
        return data
    if isinstance(data, str) and _ONLY_MACROS.match(data):
        return data
    return REDACTED
