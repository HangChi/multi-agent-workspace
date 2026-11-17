"""Redacted external tool invocation projections."""

from typing import Any, Literal

from pydantic import BaseModel, Field

InvocationStatus = Literal[
    "succeeded",
    "tool_error",
    "parameter_error",
    "transport_error",
    "auth_error",
    "timeout",
    "policy_denied",
    "cancelled",
    "result_truncated",
]


class ExternalInvocationOut(BaseModel):
    id: str
    step_id: str | None
    server_name: str
    tool_name: str
    stable_name: str
    version: str
    status: InvocationStatus
    duration_ms: int = Field(ge=0)
    error_category: str | None = None
    result_truncated: bool = False
    created_at: str


class ExternalInvocationList(BaseModel):
    items: list[ExternalInvocationOut]
    total: int


class ExternalInvocationAuditOut(BaseModel):
    id: str
    run_id: str
    owner_id: str
    agent_id: str | None = None
    server_id: str
    tool_version_id: str
    call_id: str
    status: InvocationStatus
    duration_ms: int = Field(ge=0)
    policy_result: dict[str, Any] = Field(default_factory=dict)
    error_summary: str | None = None
    created_at: str
    completed_at: str | None = None


class ExternalInvocationAuditList(BaseModel):
    items: list[ExternalInvocationAuditOut]
    total: int


class SafeInvocationSummary(BaseModel):
    """Bounded audit-safe projection; never stores raw argument/response payloads."""

    input_hash: str | None = None
    output_hash: str | None = None
    output_preview: str | None = Field(default=None, max_length=2048)
    policy_result: dict[str, Any] = Field(default_factory=dict)
