"""external notification channels (018)

Two new tables, no change to existing ones:

- ``notification_channels``: user-configured outbound targets (webhook only for
  now). ``kind`` is a varchar with a CHECK instead of an enum on purpose — 017
  learned that adding an enum value costs a migration, and 018 will very likely
  grow an ``email`` kind later (spec Out of Scope).
- ``notification_deliveries``: one row per attempt-series. Status stays
  ``pending`` until the first terminal outcome; retries bump ``attempts``.
  ``event_type`` is denormalized so a delivery row still explains what was sent
  after the notification row is gone (research R-09).

No enum values are added, so this migration needs no autocommit block.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018_notification_channels"
down_revision: str | Sequence[str] | None = "0017_project_ownership_transfer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("secret", sa.String(length=64), nullable=False),
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
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
        sa.CheckConstraint("kind IN ('webhook')", name="ck_notification_channels_kind"),
    )
    op.create_index(
        "ix_notification_channels_user_id",
        "notification_channels",
        ["user_id"],
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "channel_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("notification_channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "notification_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="ck_notification_deliveries_status",
        ),
    )
    op.create_index(
        "ix_notification_deliveries_channel_created",
        "notification_deliveries",
        ["channel_id", "created_at"],
    )
    op.create_index(
        "ix_notification_deliveries_status",
        "notification_deliveries",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_deliveries_status", table_name="notification_deliveries"
    )
    op.drop_index(
        "ix_notification_deliveries_channel_created",
        table_name="notification_deliveries",
    )
    op.drop_table("notification_deliveries")
    op.drop_index(
        "ix_notification_channels_user_id", table_name="notification_channels"
    )
    op.drop_table("notification_channels")
