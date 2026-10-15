import time
from typing import Annotated

from mcp.server.mcpserver import Context, Elicit, Resolve
from pydantic import Field

from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import ServerSettings
from icinga_mcp.models import ActionResult, SkippedTarget
from icinga_mcp.tools.actions import (
    CONFIRM_DESCRIPTION,
    TARGETS_DESCRIPTION,
    Approval,
    ResolvedTargets,
    TargetState,
    approval,
    check_author,
    duration,
    finish,
    not_applied,
    resolve_targets,
    run_action,
    target_lines,
)
from icinga_mcp.tools.base import ToolSpec, icinga_tool

TOOLSET = "acknowledgements"

Targets = Annotated[list[str], Field(min_length=1, description=TARGETS_DESCRIPTION)]
Confirm = Annotated[bool, Field(description=CONFIRM_DESCRIPTION)]


def build(client: IcingaClient, settings: ServerSettings) -> list[ToolSpec]:
    @icinga_tool
    async def resolve(targets: list[str]) -> ResolvedTargets:
        return await resolve_targets(client, settings, targets)

    def to_ack(resolved: ResolvedTargets) -> tuple[list[TargetState], list[SkippedTarget]]:
        actionable, skipped = [], []
        for t in resolved.targets:
            if not t.problem:
                skipped.append(SkippedTarget(target=t.name, reason=f"not a problem ({t.state})"))
            elif t.acknowledged:
                skipped.append(SkippedTarget(target=t.name, reason="already acknowledged"))
            else:
                actionable.append(t)
        return actionable, skipped

    def ack_message(
        targets: list[TargetState], comment: str, sticky: bool, notify: bool, expire_in: str | None
    ) -> str:
        options = [
            f"sticky={'yes' if sticky else 'no'}",
            f"notify={'yes' if notify else 'no'}",
            f"expires={'after ' + expire_in if expire_in else 'never'}",
        ]
        return (
            f"Acknowledge {len(targets)} problem(s) in Icinga as '{settings.author}':\n"
            f"{target_lines(targets)}\nComment: {comment}\nOptions: {', '.join(options)}"
        )

    @icinga_tool
    async def confirm_ack(
        resolved: Annotated[ResolvedTargets, Resolve(resolve)],
        comment: str,
        ctx: Context,
        sticky: bool = False,
        notify: bool = True,
        expire_in: str | None = None,
        confirm: bool = False,
    ) -> Approval | Elicit[Approval]:
        if expire_in:
            duration(expire_in, "expire_in")
        actionable, _ = to_ack(resolved)
        if not actionable:
            return Approval(approve=True)
        message = ack_message(actionable, comment, sticky, notify, expire_in)
        return approval(ctx, settings, message, confirm)

    @icinga_tool
    async def acknowledge_problem(
        targets: Targets,
        comment: Annotated[
            str, Field(min_length=1, description="Why the problem is acknowledged (ticket, plan)")
        ],
        resolved: Annotated[ResolvedTargets, Resolve(resolve)],
        approved: Annotated[Approval, Resolve(confirm_ack)],
        sticky: Annotated[
            bool,
            Field(
                description="Keep the acknowledgement until the object is OK again, even if "
                "it changes between problem states (e.g. WARNING -> CRITICAL)"
            ),
        ] = False,
        notify: Annotated[bool, Field(description="Send acknowledgement notifications")] = True,
        expire_in: Annotated[
            str | None,
            Field(
                description="Remove the acknowledgement automatically after this duration, "
                "e.g. '4h' or '1d'; default: no expiry"
            ),
        ] = None,
        confirm: Confirm = False,
    ) -> ActionResult:
        """Acknowledge current problems (DOWN hosts, non-OK services) so that they count as
        handled and notifications stop. Objects that are OK or already acknowledged are
        skipped. Changes Icinga: the user must confirm before anything is applied."""
        author = check_author(settings)
        expiry = time.time() + duration(expire_in, "expire_in") if expire_in else None
        actionable, skipped = to_ack(resolved)
        names = [t.name for t in actionable]
        message = ack_message(actionable, comment, sticky, notify, expire_in)
        pending = not_applied("acknowledge_problem", message, approved, names, skipped)
        if pending:
            return pending
        params: dict[str, object] = {
            "author": author,
            "comment": comment,
            "sticky": sticky,
            "notify": notify,
        }
        if expiry:
            params["expiry"] = expiry
        outcomes = await run_action(client, "acknowledge-problem", actionable, params)
        return finish("acknowledge_problem", names, skipped, outcomes)

    @icinga_tool
    async def confirm_remove(
        resolved: Annotated[ResolvedTargets, Resolve(resolve)],
        ctx: Context,
        confirm: bool = False,
    ) -> Approval | Elicit[Approval]:
        acked = [t for t in resolved.targets if t.acknowledged]
        if not acked:
            return Approval(approve=True)
        message = f"Remove the acknowledgement of {len(acked)} object(s):\n{target_lines(acked)}"
        return approval(ctx, settings, message, confirm)

    @icinga_tool
    async def remove_acknowledgement(
        targets: Targets,
        resolved: Annotated[ResolvedTargets, Resolve(resolve)],
        approved: Annotated[Approval, Resolve(confirm_remove)],
        confirm: Confirm = False,
    ) -> ActionResult:
        """Remove acknowledgements so the problems count as unhandled again and notifications
        resume. Objects without acknowledgement are skipped. Changes Icinga: the user must
        confirm before anything is applied."""
        author = check_author(settings)
        acked = [t for t in resolved.targets if t.acknowledged]
        skipped = [
            SkippedTarget(target=t.name, reason="not acknowledged")
            for t in resolved.targets
            if not t.acknowledged
        ]
        names = [t.name for t in acked]
        message = f"Remove the acknowledgement of {len(acked)} object(s):\n{target_lines(acked)}"
        pending = not_applied("remove_acknowledgement", message, approved, names, skipped)
        if pending:
            return pending
        outcomes = await run_action(client, "remove-acknowledgement", acked, {"author": author})
        return finish("remove_acknowledgement", names, skipped, outcomes)

    return [
        ToolSpec(
            acknowledge_problem, "acknowledge_problem", TOOLSET, "Acknowledge problems", write=True
        ),
        ToolSpec(
            remove_acknowledgement,
            "remove_acknowledgement",
            TOOLSET,
            "Remove acknowledgements",
            write=True,
        ),
    ]
