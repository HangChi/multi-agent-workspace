"""Add governed external MCP servers, tools, grants and invocation history.

Revision ID: 0005_external_tools
Revises: 0004_tool_grant_history
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_external_tools"
down_revision: str | Sequence[str] | None = "0004_tool_grant_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ENUM_DEFINITIONS = (
    ("external_auth_mode", ("none", "static_bearer", "static_api_key")),
    (
        "external_server_status",
        (
            "draft",
            "validation_failed",
            "pending_review",
            "published",
            "paused",
            "rejected",
            "revoked",
        ),
    ),
    ("external_validation_status", ("passed", "failed", "partial")),
    ("external_credential_kind", ("bearer", "api_key")),
    ("external_credential_status", ("active", "revoked", "superseded")),
    ("external_tool_availability", ("valid", "invalid", "stale", "revoked")),
    (
        "marketplace_listing_status",
        ("pending_review", "published", "paused", "rejected", "revoked"),
    ),
    ("external_install_status", ("installed", "disabled", "removed")),
    ("external_version_policy", ("pinned", "allow_patch")),
    ("external_grant_status", ("active", "disabled", "revoked")),
    ("external_validation_result", ("passed", "failed", "partial")),
    (
        "external_invocation_status",
        (
            "succeeded",
            "tool_error",
            "parameter_error",
            "transport_error",
            "auth_error",
            "timeout",
            "policy_denied",
            "cancelled",
            "result_truncated",
        ),
    ),
)


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in _ENUM_DEFINITIONS:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)
    op.create_table(
        "external_tool_servers",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("normalized_endpoint_url", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("maintainer", sa.String(100), nullable=False),
        sa.Column(
            "auth_mode",
            _enum("external_auth_mode", "none", "static_bearer", "static_api_key"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "external_server_status",
                "draft",
                "validation_failed",
                "pending_review",
                "published",
                "paused",
                "rejected",
                "revoked",
            ),
            nullable=False,
        ),
        sa.Column("review_reason", sa.Text()),
        sa.Column(
            "created_by",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "current_validation_status",
            _enum("external_validation_status", "passed", "failed", "partial"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "owner_id",
            "normalized_endpoint_url",
            name="uq_external_server_owner_endpoint",
        ),
    )
    op.create_index(
        "ix_external_servers_owner_status",
        "external_tool_servers",
        ["owner_id", "status"],
    )

    op.create_table(
        "external_credentials",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "server_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_servers.id"),
            nullable=False,
        ),
        sa.Column(
            "scope_owner_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column(
            "kind",
            _enum("external_credential_kind", "bearer", "api_key"),
            nullable=False,
        ),
        sa.Column("header_name", sa.String(100), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(32), nullable=False),
        sa.Column(
            "status",
            _enum("external_credential_status", "active", "revoked", "superseded"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("rotated_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "external_tool_versions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "server_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_servers.id"),
            nullable=False,
        ),
        sa.Column("external_name", sa.String(150), nullable=False),
        sa.Column("stable_name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON()),
        sa.Column("capability_category", sa.String(100)),
        sa.Column("required_permissions", sa.JSON(), nullable=False),
        sa.Column("definition_hash", sa.String(128), nullable=False),
        sa.Column(
            "availability",
            _enum("external_tool_availability", "valid", "invalid", "stale", "revoked"),
            nullable=False,
        ),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "server_id", "external_name", "version", name="uq_external_tool_version"
        ),
        sa.UniqueConstraint("stable_name", name="uq_external_tool_stable_name"),
    )
    op.create_index(
        "ix_external_versions_server_name",
        "external_tool_versions",
        ["server_id", "external_name"],
    )
    op.create_index(
        "ix_external_versions_stable_name", "external_tool_versions", ["stable_name"]
    )

    op.create_table(
        "marketplace_listings",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "tool_version_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "marketplace_listing_status",
                "pending_review",
                "published",
                "paused",
                "rejected",
                "revoked",
            ),
            nullable=False,
        ),
        sa.Column("category", sa.String(100)),
        sa.Column("usage_summary", sa.Text(), nullable=False),
        sa.Column("data_handling", sa.Text(), nullable=False),
        sa.Column("restrictions", sa.JSON(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(as_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_reason", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tool_version_id", name="uq_marketplace_listing_version"),
    )
    op.create_index("ix_marketplace_status", "marketplace_listings", ["status"])

    op.create_table(
        "external_tool_installations",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "listing_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("marketplace_listings.id"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("external_install_status", "installed", "disabled", "removed"),
            nullable=False,
        ),
        sa.Column(
            "version_policy",
            _enum("external_version_policy", "pinned", "allow_patch"),
            nullable=False,
        ),
        sa.Column(
            "installed_by",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "owner_id", "listing_id", name="uq_external_install_owner_listing"
        ),
    )
    op.create_index(
        "ix_external_install_owner",
        "external_tool_installations",
        ["owner_id", "status"],
    )

    op.create_table(
        "external_tool_grants",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "agent_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("agents.id"),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_installations.id"),
            nullable=False,
        ),
        sa.Column(
            "tool_version_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "credential_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_credentials.id"),
        ),
        sa.Column("permission_snapshot", sa.JSON(), nullable=False),
        sa.Column("schema_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            _enum("external_grant_status", "active", "disabled", "revoked"),
            nullable=False,
        ),
        sa.Column(
            "granted_by",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_external_grant_agent_status", "external_tool_grants", ["agent_id", "status"]
    )

    op.create_table(
        "external_server_validations",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "server_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_servers.id"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("external_validation_result", "passed", "failed", "partial"),
            nullable=False,
        ),
        sa.Column("connection_status", sa.String(32), nullable=False),
        sa.Column("auth_status", sa.String(32), nullable=False),
        sa.Column("discovery_status", sa.String(32), nullable=False),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("valid_count", sa.Integer(), nullable=False),
        sa.Column("definition_digest", sa.String(128)),
        sa.Column("safe_error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "run_external_tool_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "run_id", sa.Uuid(as_uuid=False), sa.ForeignKey("runs.id"), nullable=False
        ),
        sa.Column(
            "grant_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_grants.id"),
            nullable=False,
        ),
        sa.Column(
            "server_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_servers.id"),
            nullable=False,
        ),
        sa.Column(
            "tool_version_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_versions.id"),
            nullable=False,
        ),
        sa.Column("stable_name", sa.String(200), nullable=False),
        sa.Column("external_name", sa.String(150), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("permission_snapshot", sa.JSON(), nullable=False),
        sa.Column("schema_snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "credential_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_credentials.id"),
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "run_id", "stable_name", name="uq_run_external_snapshot_tool"
        ),
    )
    op.create_index(
        "ix_run_external_snapshot_run", "run_external_tool_snapshots", ["run_id"]
    )

    op.create_table(
        "external_tool_invocations",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "run_id", sa.Uuid(as_uuid=False), sa.ForeignKey("runs.id"), nullable=False
        ),
        sa.Column("step_id", sa.Uuid(as_uuid=False), sa.ForeignKey("steps.id")),
        sa.Column("agent_id", sa.Uuid(as_uuid=False), sa.ForeignKey("agents.id")),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "server_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_servers.id"),
            nullable=False,
        ),
        sa.Column(
            "tool_version_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_tool_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "grant_id", sa.Uuid(as_uuid=False), sa.ForeignKey("external_tool_grants.id")
        ),
        sa.Column(
            "credential_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("external_credentials.id"),
        ),
        sa.Column("call_id", sa.String(100), nullable=False),
        sa.Column(
            "status",
            _enum(
                "external_invocation_status",
                "succeeded",
                "tool_error",
                "parameter_error",
                "transport_error",
                "auth_error",
                "timeout",
                "policy_denied",
                "cancelled",
                "result_truncated",
            ),
            nullable=False,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("input_summary", sa.JSON()),
        sa.Column("output_summary", sa.JSON()),
        sa.Column("policy_result", sa.JSON(), nullable=False),
        sa.Column("error_summary", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_external_invocation_run_created",
        "external_tool_invocations",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_external_invocation_server_created",
        "external_tool_invocations",
        ["server_id", "created_at"],
    )
    op.create_index(
        "ix_external_invocation_tool_created",
        "external_tool_invocations",
        ["tool_version_id", "created_at"],
    )
    op.create_index(
        "ix_external_invocation_owner_created",
        "external_tool_invocations",
        ["owner_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("external_tool_invocations")
    op.drop_table("run_external_tool_snapshots")
    op.drop_table("external_server_validations")
    op.drop_table("external_tool_grants")
    op.drop_table("external_tool_installations")
    op.drop_table("marketplace_listings")
    op.drop_table("external_tool_versions")
    op.drop_table("external_credentials")
    op.drop_table("external_tool_servers")
    # PostgreSQL named enums are not removed by drop_table; make downgrade
    # genuinely reversible for isolated test/deployment databases.
    for name in (
        "external_invocation_status",
        "external_validation_result",
        "external_grant_status",
        "external_version_policy",
        "external_install_status",
        "marketplace_listing_status",
        "external_tool_availability",
        "external_credential_status",
        "external_credential_kind",
        "external_validation_status",
        "external_server_status",
        "external_auth_mode",
    ):
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
