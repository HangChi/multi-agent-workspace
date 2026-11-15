"""Database models for governed external MCP servers and grants."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


def _uuid() -> str:
    return str(uuid.uuid4())


class ExternalToolServer(Base):
    __tablename__ = "external_tool_servers"
    __table_args__ = (Index("ix_external_servers_owner_status", "owner_id", "status"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_endpoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    maintainer: Mapped[str] = mapped_column(String(100), nullable=False)
    auth_mode: Mapped[str] = mapped_column(Enum("none", "static_bearer", "static_api_key", name="external_auth_mode"), nullable=False)
    status: Mapped[str] = mapped_column(Enum("draft", "validation_failed", "pending_review", "published", "paused", "rejected", "revoked", name="external_server_status"), nullable=False, default="draft")
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_validation_status: Mapped[str | None] = mapped_column(Enum("passed", "failed", "partial", name="external_validation_status"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tools = relationship("ExternalToolVersion", back_populates="server", cascade="all, delete-orphan")
    credentials = relationship("ExternalCredential", back_populates="server", cascade="all, delete-orphan")
    validations = relationship("ServerValidation", back_populates="server", cascade="all, delete-orphan")


class ExternalToolVersion(Base):
    __tablename__ = "external_tool_versions"
    __table_args__ = (
        Index("ix_external_versions_server_name", "server_id", "external_name"),
        Index("ix_external_versions_stable_name", "stable_name"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_uuid)
    server_id: Mapped[str] = mapped_column(ForeignKey("external_tool_servers.id"), nullable=False)
    external_name: Mapped[str] = mapped_column(String(150), nullable=False)
    stable_name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    capability_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    required_permissions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    definition_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    availability: Mapped[str] = mapped_column(Enum("valid", "invalid", "stale", "revoked", name="external_tool_availability"), nullable=False, default="valid")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    server = relationship("ExternalToolServer", back_populates="tools")
    listings = relationship("MarketplaceListing", back_populates="tool_version", cascade="all, delete-orphan")


class MarketplaceListing(Base):
    __tablename__ = "marketplace_listings"
    __table_args__ = (Index("ix_marketplace_status", "status"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_uuid)
    tool_version_id: Mapped[str] = mapped_column(ForeignKey("external_tool_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(Enum("pending_review", "published", "paused", "rejected", "revoked", name="marketplace_listing_status"), nullable=False, default="pending_review")
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    usage_summary: Mapped[str] = mapped_column(Text, nullable=False)
    data_handling: Mapped[str] = mapped_column(Text, nullable=False)
    restrictions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tool_version = relationship("ExternalToolVersion", back_populates="listings")
    installations = relationship("ToolInstallation", back_populates="listing", cascade="all, delete-orphan")


class ToolInstallation(Base):
    __tablename__ = "external_tool_installations"
    __table_args__ = (Index("ix_external_install_owner", "owner_id", "status"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_uuid)
    listing_id: Mapped[str] = mapped_column(ForeignKey("marketplace_listings.id"), nullable=False)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(Enum("installed", "disabled", "removed", name="external_install_status"), nullable=False, default="installed")
    version_policy: Mapped[str] = mapped_column(Enum("pinned", "allow_patch", name="external_version_policy"), nullable=False, default="pinned")
    installed_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    listing = relationship("MarketplaceListing", back_populates="installations")
    grants = relationship("ExternalToolGrant", back_populates="installation", cascade="all, delete-orphan")


class ExternalToolGrant(Base):
    __tablename__ = "external_tool_grants"
    __table_args__ = (Index("ix_external_grant_agent_status", "agent_id", "status"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    installation_id: Mapped[str] = mapped_column(ForeignKey("external_tool_installations.id"), nullable=False)
    tool_version_id: Mapped[str] = mapped_column(ForeignKey("external_tool_versions.id"), nullable=False)
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("external_credentials.id"), nullable=True)
    permission_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    schema_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(Enum("active", "disabled", "revoked", name="external_grant_status"), nullable=False, default="active")
    granted_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    installation = relationship("ToolInstallation", back_populates="grants")
    tool_version = relationship("ExternalToolVersion")
    credential = relationship("ExternalCredential")


class RunExternalToolSnapshot(Base):
    __tablename__ = "run_external_tool_snapshots"
    __table_args__ = (Index("ix_run_external_snapshot_run", "run_id"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    grant_id: Mapped[str] = mapped_column(ForeignKey("external_tool_grants.id"), nullable=False)
    server_id: Mapped[str] = mapped_column(ForeignKey("external_tool_servers.id"), nullable=False)
    tool_version_id: Mapped[str] = mapped_column(ForeignKey("external_tool_versions.id"), nullable=False)
    stable_name: Mapped[str] = mapped_column(String(200), nullable=False)
    external_name: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    permission_snapshot: Mapped[list] = mapped_column(JSON, nullable=False)
    schema_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    credential_id: Mapped[str | None] = mapped_column(ForeignKey("external_credentials.id"), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
