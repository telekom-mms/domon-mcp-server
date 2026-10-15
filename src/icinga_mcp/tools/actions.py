"""Shared building blocks for tools that change Icinga (acknowledgements, comments, downtimes).

Safety model:
- Targets are explicit object names (``host`` or ``host!service``), matched exactly via
  ``filter_vars``; there are no patterns and never an empty filter.
- All targets are resolved before anything happens; unknown names abort the call.
- The user confirms every change: via elicitation if the client supports it, otherwise the
  first call returns a preview and only a second call with ``confirm=true`` applies it.
"""

from collections.abc import Sequence
from typing import Any

from mcp.server.mcpserver import Context, Elicit
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from icinga_mcp import formatting as fmt
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import ServerSettings
from icinga_mcp.models import ActionOutcome, ActionResult, SkippedTarget
from icinga_mcp.timeparse import parse_duration, parse_time

CONFIRM_DESCRIPTION = (
    "Leave false. Only for clients that cannot ask the user interactively: the first call "
    "returns a preview; after the user explicitly approved it, call again with confirm=true."
)
TARGETS_DESCRIPTION = (
    "Exact object names: 'host' for a host or 'host!service' for a service "
    "(as returned by list_problems / get_host; no patterns)."
)


class Approval(BaseModel):
    approve: bool = Field(description="Apply this change in Icinga?")


class TargetState(BaseModel):
    name: str
    is_service: bool
    problem: bool
    acknowledged: bool
    in_downtime: bool
    state: str


class ResolvedTargets(BaseModel):
    targets: list[TargetState]


def check_author(settings: ServerSettings) -> str:
    if not settings.author:
        raise ToolError("ICINGA_MCP_AUTHOR is not configured; write tools need an author.")
    return settings.author


def duration(text: str, what: str) -> int:
    try:
        return parse_duration(text)
    except ValueError as exc:
        raise ToolError(f"{what}: {exc}") from None


def point_in_time(text: str, what: str, now: float) -> float:
    try:
        return parse_time(text, now)
    except ValueError as exc:
        raise ToolError(f"{what}: {exc}") from None


def normalize_names(names: Sequence[str], settings: ServerSettings, what: str) -> list[str]:
    cleaned = sorted({name.strip() for name in names if name.strip()})
    if not cleaned:
        raise ToolError(f"No {what} given.")
    if len(cleaned) > settings.max_targets:
        raise ToolError(
            f"{len(cleaned)} {what} given, at most {settings.max_targets} are allowed per call "
            "(ICINGA_MCP_MAX_TARGETS)."
        )
    return cleaned


async def resolve_targets(
    client: IcingaClient, settings: ServerSettings, targets: Sequence[str]
) -> ResolvedTargets:
    names = normalize_names(targets, settings, "targets")
    host_names = [n for n in names if "!" not in n]
    service_names = [n for n in names if "!" in n]
    attrs = ["state", "problem", "acknowledgement", "downtime_depth"]
    found: dict[str, TargetState] = {}
    if host_names:
        for obj in await client.query_objects(
            "hosts", filter="host.name in names", filter_vars={"names": host_names}, attrs=attrs
        ):
            found[obj["name"]] = _target_state(obj, is_service=False)
    if service_names:
        for obj in await client.query_objects(
            "services",
            filter="service.__name in names",
            filter_vars={"names": service_names},
            attrs=attrs,
        ):
            found[obj["name"]] = _target_state(obj, is_service=True)
    missing = [n for n in names if n not in found]
    if missing:
        raise ToolError(
            f"Unknown object(s): {', '.join(missing)}. Nothing was changed. Use exact names "
            "('host' or 'host!service') from list_problems or get_host."
        )
    return ResolvedTargets(targets=[found[n] for n in names])


def approval(
    ctx: Context, settings: ServerSettings, message: str, confirm: bool
) -> Approval | Elicit[Approval]:
    """Ask the user via elicitation, or fall back to the two-step ``confirm`` flow."""
    capabilities = ctx.client_capabilities
    if capabilities is not None and capabilities.elicitation is not None:
        return Elicit(message, Approval)
    if settings.require_elicitation:
        raise ToolError(
            "This client cannot ask the user for confirmation and ICINGA_MCP_REQUIRE_ELICITATION "
            "is enabled, so no changes are possible from here. Nothing was changed."
        )
    return Approval(approve=confirm)


def target_lines(targets: Sequence[TargetState]) -> str:
    return "\n".join(f"- {t.name} ({t.state})" for t in targets)


def not_applied(
    action: str, message: str, approved: Approval, targets: list[str], skipped: list[SkippedTarget]
) -> ActionResult | None:
    """Result for calls that do not reach Icinga (nothing to do, preview, user said no)."""
    if not targets:
        return ActionResult(
            action=action,
            applied=False,
            message="Nothing to do.",
            targets=[],
            skipped=skipped,
            results=[],
        )
    if approved.approve:
        return None
    return ActionResult(
        action=action,
        applied=False,
        message=f"Not applied. Pending confirmation:\n{message}\n\nShow this to the user. Only "
        "if the user explicitly approves, call the tool again with the same arguments and "
        "confirm=true.",
        targets=targets,
        skipped=skipped,
        results=[],
    )


async def run_action(
    client: IcingaClient,
    action: str,
    targets: Sequence[TargetState],
    params: dict[str, Any],
) -> list[ActionOutcome]:
    """Run an Icinga action for host and service targets (one API call per object type)."""
    outcomes: list[ActionOutcome] = []
    hosts = [t.name for t in targets if not t.is_service]
    services = [t.name for t in targets if t.is_service]
    for object_type, flt, names in (
        ("Host", "host.name in names", hosts),
        ("Service", "service.__name in names", services),
    ):
        if names:
            body = {"type": object_type, "filter": flt, "filter_vars": {"names": names}, **params}
            outcomes += outcomes_from(await client.action(action, body))
    return outcomes


async def run_object_action(
    client: IcingaClient, action: str, body: dict[str, Any]
) -> list[ActionOutcome]:
    return outcomes_from(await client.action(action, body))


def outcomes_from(results: list[dict[str, Any]]) -> list[ActionOutcome]:
    return [
        ActionOutcome(
            code=int(r.get("code") or 0),
            status=fmt.truncate(r.get("status") or r.get("errors") or "", 500),
            name=r.get("name") or None,
        )
        for r in results
    ]


def finish(
    action: str,
    targets: list[str],
    skipped: list[SkippedTarget],
    outcomes: list[ActionOutcome],
) -> ActionResult:
    failed = [o for o in outcomes if o.code >= 400]
    ok = len(outcomes) - len(failed)
    message = f"{ok} of {len(outcomes)} succeeded."
    if failed:
        message += " Failed: " + "; ".join(o.status for o in failed)
    return ActionResult(
        action=action,
        applied=ok > 0,
        message=message,
        targets=targets,
        skipped=skipped,
        results=outcomes,
    )


def _target_state(obj: dict[str, Any], *, is_service: bool) -> TargetState:
    attrs = obj.get("attrs", {})
    state = (fmt.service_state if is_service else fmt.host_state)(attrs.get("state"))
    return TargetState(
        name=obj["name"],
        is_service=is_service,
        problem=bool(attrs.get("problem")),
        acknowledged=bool(attrs.get("acknowledgement")),
        in_downtime=bool(attrs.get("downtime_depth")),
        state=state,
    )
