from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

# "pass" must not hit Icinga attributes such as enable_passive_checks.
_SENSITIVE_KEY = re.compile(
    r"(?<!by)pass(?!ive)|secret|token|basic_?auth|api_?key|private_?key|credential",
    re.IGNORECASE,
)


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY.search(key))


def redact(data: Any) -> Any:
    """Return a copy of ``data`` with values of sensitive-looking keys masked.

    Booleans and ``None`` are kept so flags like ``use_password: true`` stay readable.
    """
    if isinstance(data, dict):
        return {
            key: (
                REDACTED
                if isinstance(key, str)
                and is_sensitive_key(key)
                and value is not None
                and not isinstance(value, bool)
                else redact(value)
            )
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact(item) for item in data]
    return data
