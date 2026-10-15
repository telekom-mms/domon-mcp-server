from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from icinga_mcp.clients.director import DirectorClient, ObjectType
from icinga_mcp.config import ServerSettings
from icinga_mcp.models import DirectorList, DirectorObject, DirectorSummary
from icinga_mcp.redaction import redact
from icinga_mcp.tools.base import ToolSpec, icinga_tool

TOOLSET = "director"
MAX_REFERENCES = 100

TypeParam = Annotated[
    ObjectType,
    Field(
        description="Kind of Director object. Services are defined by service_apply_rule "
        "(assign_filter decides which hosts get them) based on service_template"
    ),
]

# Who can reference a template or command: (object type, attribute holding the reference)
_REFERENCES: dict[str, list[tuple[str, str]]] = {
    "host_template": [("host", "imports"), ("host_template", "imports")],
    "service_template": [("service_template", "imports"), ("service_apply_rule", "imports")],
    "notification_template": [("notification_template", "imports")],
    "command": [
        ("host_template", "check_command"),
        ("host", "check_command"),
        ("service_template", "check_command"),
        ("service_apply_rule", "check_command"),
        ("command", "imports"),
    ],
}
_SEARCH_FIELDS = (
    "object_name",
    "display_name",
    "address",
    "check_command",
    "command",
    "assign_filter",
)


def build(director: DirectorClient, settings: ServerSettings) -> list[ToolSpec]:
    @icinga_tool
    async def director_list(
        object_type: TypeParam,
        search: Annotated[
            str | None,
            Field(
                description="Case-insensitive text matched against name, display name, "
                "imports, address, check command, command line and assign filter"
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> DirectorList:
        """Search the Icinga Director configuration (what is configured, not the runtime
        state): hosts, templates, service apply rules, commands, host groups, notification
        templates, time periods. E.g. find the apply rules whose assign filter mentions a
        host, or the templates a host imports. Use director_get for details."""
        objects = await director.list(object_type)
        if search:
            needle = search.lower()
            objects = [o for o in objects if needle in _search_text(o)]
        objects = sorted(objects, key=lambda o: str(o.get("object_name", "")))
        return DirectorList(
            object_type=object_type,
            total=len(objects),
            returned=min(len(objects), limit),
            truncated=len(objects) > limit,
            objects=[_summary(o) for o in objects[:limit]],
        )

    @icinga_tool
    async def director_get(
        object_type: TypeParam,
        name: Annotated[str, Field(min_length=1, description="Exact Director object name")],
        resolved: Annotated[
            bool,
            Field(
                description="Also return the properties inherited from templates "
                "(hosts and templates only)"
            ),
        ] = True,
    ) -> DirectorObject:
        """Full Director definition of one object: imports, check command, intervals,
        custom variables (secrets redacted), assign filter, command arguments. For templates
        and commands it also lists which objects use them."""
        matches = [o for o in await director.list(object_type) if o.get("object_name") == name]
        if not matches:
            raise ToolError(
                f"No Director {object_type} named '{name}'. Use director_list to search."
            )
        properties = matches[0]
        inherited = await director.resolved(object_type, name) if resolved else None
        references = await _references(director, object_type, name)
        return DirectorObject(
            object_type=object_type,
            name=name,
            properties=redact(_clean(properties)),
            resolved=redact(_clean(inherited)) if inherited else None,
            referenced_by=references[:MAX_REFERENCES],
            referenced_by_truncated=len(references) > MAX_REFERENCES,
        )

    return [
        ToolSpec(director_get, "director_get", TOOLSET, "Director object details"),
        ToolSpec(director_list, "director_list", TOOLSET, "Search Director configuration"),
    ]


async def _references(director: DirectorClient, object_type: str, name: str) -> list[str]:
    found: list[str] = []
    for other_type, attribute in _REFERENCES.get(object_type, []):
        for obj in await director.list(other_type):
            value = obj.get(attribute)
            if value == name or (isinstance(value, list) and name in value):
                found.append(f"{other_type}:{obj.get('object_name')}")
    return sorted(found)


def _search_text(obj: dict[str, Any]) -> str:
    parts = [str(obj.get(field) or "") for field in _SEARCH_FIELDS]
    parts += [str(item) for item in obj.get("imports") or []]
    return "\n".join(parts).lower()


def _summary(obj: dict[str, Any]) -> DirectorSummary:
    command = obj.get("command")
    return DirectorSummary(
        name=str(obj.get("object_name", "")),
        director_type=obj.get("object_type"),
        imports=[str(i) for i in obj.get("imports") or []],
        display_name=obj.get("display_name") or None,
        address=obj.get("address") or None,
        check_command=obj.get("check_command") or None,
        command=" ".join(command) if isinstance(command, list) else command or None,
        assign_filter=(obj.get("assign_filter") or "").strip() or None,
    )


def _clean(obj: dict[str, Any]) -> dict[str, Any]:
    """Drop internal noise (uuid, empty field definitions)."""
    return {k: v for k, v in obj.items() if k != "uuid" and not (k == "fields" and not v)}
