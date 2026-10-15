from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase

READ_PERMISSIONS: tuple[str, ...] = (
    "status/query",
    "objects/query/Host",
    "objects/query/Service",
    "objects/query/HostGroup",
    "objects/query/ServiceGroup",
    "objects/query/Comment",
    "objects/query/Downtime",
)

WRITE_PERMISSIONS: tuple[str, ...] = (
    "actions/acknowledge-problem",
    "actions/remove-acknowledgement",
    "actions/add-comment",
    "actions/schedule-downtime",
    "actions/remove-downtime",
)


def is_granted(required: str, granted: Iterable[str]) -> bool:
    """Icinga permissions may contain wildcards, e.g. ``objects/query/*`` or ``*``."""
    return any(fnmatchcase(required, pattern) for pattern in granted)


def missing(required: Iterable[str], granted: Iterable[str]) -> list[str]:
    granted = list(granted)
    return [perm for perm in required if not is_granted(perm, granted)]


def unused(granted: Iterable[str], known: Iterable[str]) -> list[str]:
    """Granted permissions that cover none of the permissions icinga-mcp uses."""
    known = list(known)
    return [pattern for pattern in granted if not any(fnmatchcase(k, pattern) for k in known)]
