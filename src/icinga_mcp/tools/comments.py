from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, Elicit, Resolve
from pydantic import Field

from icinga_mcp import formatting as fmt
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.config import ServerSettings
from icinga_mcp.models import ActionResult, Comment, CommentList
from icinga_mcp.tools.actions import (
    CONFIRM_DESCRIPTION,
    TARGETS_DESCRIPTION,
    Approval,
    ResolvedTargets,
    approval,
    check_author,
    finish,
    not_applied,
    resolve_targets,
    run_action,
    target_lines,
)
from icinga_mcp.tools.base import Filter, ToolSpec, icinga_tool

TOOLSET = "comments"

CommentType = Literal["user", "downtime", "flapping", "acknowledgement"]
COMMENT_TYPE_CODES = {name: code for code, name in fmt.COMMENT_TYPES.items()}


def build(client: IcingaClient, settings: ServerSettings) -> list[ToolSpec]:
    @icinga_tool
    async def list_comments(
        host: Annotated[str | None, Field(description="Host name or glob pattern")] = None,
        service: Annotated[
            str | None,
            Field(description="Service name or glob pattern; omit for host and service comments"),
        ] = None,
        author: Annotated[str | None, Field(description="Author name or glob pattern")] = None,
        entry_type: Annotated[
            CommentType | None,
            Field(
                description="Only comments of this type (acknowledgement comments hold the "
                "ack text)"
            ),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> CommentList:
        """List comments on hosts and services (user notes, acknowledgement texts,
        downtime and flapping comments), newest first."""
        flt = Filter()
        if host:
            flt.add("match(host_pattern, comment.host_name)", host_pattern=host)
        if service:
            flt.add("match(service_pattern, comment.service_name)", service_pattern=service)
        if author:
            flt.add("match(author_pattern, comment.author)", author_pattern=author)
        if entry_type:
            flt.add("comment.entry_type == entry_type", entry_type=COMMENT_TYPE_CODES[entry_type])
        comments = await client.query_objects(
            "comments",
            filter=flt.expression,
            filter_vars=flt.vars,
            attrs=[
                "__name",
                "host_name",
                "service_name",
                "author",
                "text",
                "entry_type",
                "entry_time",
                "expire_time",
            ],
        )
        comments.sort(key=lambda c: -float(c.get("attrs", {}).get("entry_time") or 0))
        return CommentList(
            total=len(comments),
            returned=min(len(comments), limit),
            truncated=len(comments) > limit,
            comments=[_comment(c.get("attrs", {})) for c in comments[:limit]],
        )

    @icinga_tool
    async def resolve(targets: list[str]) -> ResolvedTargets:
        return await resolve_targets(client, settings, targets)

    def comment_message(resolved: ResolvedTargets, comment: str) -> str:
        return (
            f"Add a comment to {len(resolved.targets)} object(s) in Icinga as "
            f"'{settings.author}':\n{target_lines(resolved.targets)}\nComment: {comment}"
        )

    @icinga_tool
    async def confirm_comment(
        resolved: Annotated[ResolvedTargets, Resolve(resolve)],
        comment: str,
        ctx: Context,
        confirm: bool = False,
    ) -> Approval | Elicit[Approval]:
        return approval(ctx, settings, comment_message(resolved, comment), confirm)

    @icinga_tool
    async def add_comment(
        targets: Annotated[list[str], Field(min_length=1, description=TARGETS_DESCRIPTION)],
        comment: Annotated[str, Field(min_length=1, description="Comment text (a note)")],
        resolved: Annotated[ResolvedTargets, Resolve(resolve)],
        approved: Annotated[Approval, Resolve(confirm_comment)],
        confirm: Annotated[bool, Field(description=CONFIRM_DESCRIPTION)] = False,
    ) -> ActionResult:
        """Add a comment (note) to hosts or services, e.g. findings of an analysis or a
        ticket reference. Visible to everyone in Icinga Web. Changes Icinga: the user must
        confirm before anything is applied."""
        author = check_author(settings)
        names = [t.name for t in resolved.targets]
        pending = not_applied(
            "add_comment", comment_message(resolved, comment), approved, names, []
        )
        if pending:
            return pending
        outcomes = await run_action(
            client, "add-comment", resolved.targets, {"author": author, "comment": comment}
        )
        return finish("add_comment", names, [], outcomes)

    return [
        ToolSpec(list_comments, "list_comments", TOOLSET, "List comments"),
        ToolSpec(
            add_comment, "add_comment", TOOLSET, "Add a comment", write=True, idempotent=False
        ),
    ]


def _comment(attrs: dict[str, Any]) -> Comment:
    return Comment(
        name=attrs.get("__name", ""),
        host=attrs.get("host_name", ""),
        service=attrs.get("service_name") or None,
        author=attrs.get("author", ""),
        text=fmt.truncate(attrs.get("text", "")),
        entry_type=fmt.comment_type(attrs.get("entry_type")),
        entry_time=fmt.timestamp(attrs.get("entry_time")),
        expire_time=fmt.timestamp(attrs.get("expire_time")),
    )
