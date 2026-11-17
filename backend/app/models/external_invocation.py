"""Immutable audit facts for external MCP tool calls."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class ExternalToolInvocation(Base):
    __tablename__ = "external_tool_invocations"
    __table_args__ = (
        Index("ix_external_invocation_run_created", "run_id", "created_at"),
        Index("ix_external_invocation_server_created", "server_id", "created_at"),
        Index("ix_external_invocation_tool_created", "tool_version_id", "created_at"),
        Index("ix_external_invocation_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("steps.id"), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    server_id: Mapped[str] = mapped_column(
        ForeignKey("external_tool_servers.id"), nullable=False
    )
    tool_version_id: Mapped[str] = mapped_column(
        ForeignKey("external_tool_versions.id"), nullable=False
    )
    grant_id: Mapped[str | None] = mapped_column(
        ForeignKey("external_tool_grants.id"), nullable=True
    )
    credential_id: Mapped[str | None] = mapped_column(
        ForeignKey("external_credentials.id"), nullable=True
    )
    call_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(
            "succeeded",
            "tool_error",
            "parameter_error",
            "transport_error",
            "auth_error",
            "timeout",
            "policy_denied",
            "cancelled",
            "result_truncated",
            name="external_invocation_status",
        ),
        nullable=False,
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    server = relationship("ExternalToolServer")
    tool_version = relationship("ExternalToolVersion")
