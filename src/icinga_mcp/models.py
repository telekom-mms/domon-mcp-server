"""Pydantic models returned by the tools (exposed as MCP ``outputSchema``)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr


class Problem(BaseModel):
    type: Literal["host", "service"]
    host: str
    service: str | None = Field(None, description="Service name (None for host problems)")
    display_name: str
    state: str = Field(description="DOWN/UNREACHABLE for hosts, WARNING/CRITICAL/UNKNOWN")
    state_type: Literal["hard", "soft"]
    handled: bool = Field(description="Acknowledged, in downtime or otherwise handled")
    acknowledged: bool
    in_downtime: bool
    flapping: bool
    since: str | None = Field(None, description="Time of the last state change (ISO 8601)")
    duration: str | None = Field(None, description="Time since the last state change")
    last_check: str | None = None
    output: str = Field(description="Plugin output of the last check (truncated)")


class ProblemSummary(BaseModel):
    total: int
    unhandled: int
    handled: int
    by_state: dict[str, int]


class ProblemList(BaseModel):
    summary: ProblemSummary = Field(description="Counts over all problems matching the filters")
    returned: int
    truncated: bool
    problems: list[Problem]


class StateCounts(BaseModel):
    total: int
    by_state: dict[str, int]
    unhandled_problems: int


class IcingaStatus(BaseModel):
    version: str | None
    node: str | None
    program_start: str | None
    uptime: str | None
    notifications_enabled: bool | None
    active_host_checks_enabled: bool | None
    active_service_checks_enabled: bool | None
    event_handlers_enabled: bool | None
    flap_detection_enabled: bool | None
    hosts: StateCounts
    services: StateCounts


class ServiceBrief(BaseModel):
    name: str
    display_name: str
    state: str
    state_type: Literal["hard", "soft"]
    handled: bool
    since: str | None
    output: str


class HostBrief(BaseModel):
    name: str
    display_name: str
    address: str | None
    state: str
    handled: bool
    groups: list[str]


class HostList(BaseModel):
    total: int
    returned: int
    truncated: bool
    hosts: list[HostBrief]


class ObjectDetails(BaseModel):
    name: str
    display_name: str
    state: str
    state_type: Literal["hard", "soft"]
    handled: bool
    acknowledged: bool
    in_downtime: bool
    flapping: bool
    since: str | None
    duration: str | None
    last_hard_state_change: str | None
    last_check: str | None
    next_check: str | None
    check_attempt: str = Field(description="current/max check attempts")
    check_command: str | None
    check_interval_seconds: float | None
    output: str
    performance_data: list[str]
    groups: list[str]
    templates: list[str]
    zone: str | None
    notes: str | None
    notes_url: str | None
    action_url: str | None
    enable_notifications: bool | None
    enable_active_checks: bool | None
    vars: dict[str, Any] = Field(description="Custom variables (sensitive values redacted)")


class HostDetails(ObjectDetails):
    address: str | None
    address6: str | None
    service_summary: dict[str, int]
    services: list[ServiceBrief] = Field(description="Services of this host, problems first")
    services_truncated: bool


class ServiceDetails(ObjectDetails):
    host: str
    host_state: str | None


class Comment(BaseModel):
    name: str = Field(description="Icinga object name of the comment")
    host: str
    service: str | None
    author: str
    text: str
    entry_type: str = Field(description="user, downtime, flapping or acknowledgement")
    entry_time: str | None
    expire_time: str | None


class CommentList(BaseModel):
    total: int
    returned: int
    truncated: bool
    comments: list[Comment]


class Downtime(BaseModel):
    name: str = Field(description="Icinga object name of the downtime (needed to remove it)")
    host: str
    service: str | None
    author: str
    comment: str
    start: str | None
    end: str | None
    fixed: bool
    duration: str | None = Field(None, description="Duration for flexible downtimes")
    in_effect: bool
    triggered_by: str | None


class DowntimeList(BaseModel):
    total: int
    returned: int
    truncated: bool
    downtimes: list[Downtime]


class SkippedTarget(BaseModel):
    target: str
    reason: str


class ActionOutcome(BaseModel):
    code: int = Field(description="Icinga result code per object (200 = success)")
    status: str
    name: str | None = Field(None, description="Name of a created object, e.g. a downtime")


class ActionResult(BaseModel):
    action: str
    applied: bool = Field(description="True if Icinga was changed")
    message: str
    targets: list[str] = Field(description="Objects the action was (or would be) applied to")
    skipped: list[SkippedTarget]
    results: list[ActionOutcome]


class HistoryEvent(BaseModel):
    time: str = Field(description="ISO 8601 time of the event")
    category: str = Field(description="state, soft_state, notification, acknowledgement, ...")
    event: str = Field(description="What happened, e.g. 'state change', 'acknowledged'")
    host: str
    service: str | None
    state: str | None = Field(None, description="New state for state changes and notifications")
    attempt: str | None = Field(None, description="Check attempt for soft state changes")
    actor: str | None = Field(None, description="Author or notified contact")
    output: str = Field(description="Plugin output or comment text (truncated)")
    _timestamp: float = PrivateAttr(default=0.0)

    @property
    def timestamp(self) -> float:
        return self._timestamp


class ObjectActivity(BaseModel):
    object: str = Field(description="'host' or 'host!service'")
    state_changes: int
    problem_changes: int = Field(description="Hard state changes into a non-OK state")
    last_state: str | None
    last_change: str | None


class HistorySummary(BaseModel):
    total: int = Field(description="All events in the window matching the filters")
    by_event: dict[str, int]
    state_changes_by_state: dict[str, int] = Field(
        description="Hard state changes per new state (OK/UP = recoveries)"
    )
    most_active: list[ObjectActivity] = Field(
        description="Objects with the most hard state changes (flapping candidates)"
    )


class HistoryResult(BaseModel):
    start: str
    end: str
    summary: HistorySummary
    returned: int
    truncated: bool
    events: list[HistoryEvent] = Field(description="Newest first")
