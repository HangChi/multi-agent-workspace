"""Protected metadata for static MCP server credentials."""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class ExternalCredential(Base):
    __tablename__ = "external_credentials"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    server_id: Mapped[str] = mapped_column(
        ForeignKey("external_tool_servers.id"), nullable=False
    )
    scope_owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(
        Enum("bearer", "api_key", name="external_credential_kind"), nullable=False
    )
    header_name: Mapped[str] = mapped_column(String(100), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("active", "revoked", "superseded", name="external_credential_status"),
        nullable=False,
        default="active",
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    server = relationship("ExternalToolServer", back_populates="credentials")
