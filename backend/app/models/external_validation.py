"""Validation history for external MCP servers."""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class ServerValidation(Base):
    __tablename__ = "external_server_validations"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_uuid)
    server_id: Mapped[str] = mapped_column(ForeignKey("external_tool_servers.id"), nullable=False)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(Enum("passed", "failed", "partial", name="external_validation_result"), nullable=False)
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False)
    auth_status: Mapped[str] = mapped_column(String(32), nullable=False)
    discovery_status: Mapped[str] = mapped_column(String(32), nullable=False)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    definition_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    safe_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    server = relationship("ExternalToolServer", back_populates="validations")
