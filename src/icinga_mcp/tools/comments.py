from typing import Annotated, Any, Literal

from pydantic import Field

from icinga_mcp import formatting as fmt
from icinga_mcp.clients.icinga_api import IcingaClient
from icinga_mcp.models import Comment, CommentList
from icinga_mcp.tools.base import Filter, ToolSpec, icinga_tool

TOOLSET = "comments"

CommentType = Literal["user", "downtime", "flapping", "acknowledgement"]
COMMENT_TYPE_CODES = {name: code for code, name in fmt.COMMENT_TYPES.items()}


def build(client: IcingaClient) -> list[ToolSpec]:
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

    return [ToolSpec(list_comments, "list_comments", TOOLSET, "List comments")]


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
