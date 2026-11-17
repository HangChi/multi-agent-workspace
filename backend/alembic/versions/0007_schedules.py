"""Add scheduled requests: rules, trigger facts, and a request source link.

Three things are added, in dependency order:

1. Two named enums (``schedule_target_type``, ``schedule_trigger_result``).
   ``schedule_trigger_result`` is also referenced by ``schedules.last_result``
   (a denormalised copy so the list page never joins the history table), so the
   type has to exist before either table.
2. ``schedules``: the mutable rule. Includes a CHECK that an enabled rule always
   has ``next_run_at`` — without it a rule can sit in "enabled" with no next
   fire time, and it will never be picked up by the scan while looking perfectly
   healthy in the UI.
3. ``schedule_triggers``: append-only facts. Its UNIQUE
   ``(schedule_id, planned_at)`` is the whole idempotency mechanism: the same
   planned instant can never produce two requests, no matter how the scan is
   retried, raced or duplicated.
4. ``requests.source_schedule_id`` with ON DELETE SET NULL. Deleting a rule must
   not remove the requests it already produced (and through them their runs,
   steps and artifacts).

The enum types are dropped explicitly on downgrade; PostgreSQL does not remove
them with the tables, and leaving them behind makes ``upgrade`` fail on the next
round-trip (the 002/003 migrations already follow this convention).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_schedules"
down_revision: str | Sequence[str] | None = "0006_artifact_run_step_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ENUM_DEFINITIONS = (
    ("schedule_target_type", ("agent", "group")),
    (
        "schedule_trigger_result",
        ("fired", "missed", "skipped_busy", "failed"),
    ),
)


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in _ENUM_DEFINITIONS:
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "schedules",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cron_expr", sa.String(120), nullable=False),
        sa.Column(
            "timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"
        ),
        sa.Column("template_title", sa.String(200), nullable=False),
        sa.Column("template_description", sa.Text(), nullable=True),
        sa.Column("template_expected_output", sa.Text(), nullable=True),
        # Reuses the request_priority type created in 0001; create_type=False
        # keeps Alembic from emitting a second CREATE TYPE for it.
        sa.Column(
            "template_priority",
            _enum("request_priority", "high", "normal", "low"),
            nullable=False,
            server_default="normal",
        ),
        sa.Column(
            "target_type",
            _enum("schedule_target_type", "agent", "group"),
            nullable=False,
        ),
        sa.Column("target_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("target_strategy", sa.String(32), nullable=True),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_result",
            _enum(
                "schedule_trigger_result", "fired", "missed", "skipped_busy", "failed"
            ),
            nullable=True,
        ),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("auto_disabled_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("name <> ''", name="ck_schedules_name_not_blank"),
        sa.CheckConstraint("cron_expr <> ''", name="ck_schedules_cron_not_blank"),
        sa.CheckConstraint(
            "template_title <> ''", name="ck_schedules_template_title_not_blank"
        ),
        # An enabled rule without a next fire time is invisible to the scan and
        # therefore silently dead. Fail closed at the schema level instead.
        sa.CheckConstraint(
            "not enabled or next_run_at is not null",
            name="ck_schedules_enabled_requires_next_run",
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0", name="ck_schedules_failures_non_negative"
        ),
        sa.CheckConstraint(
            "target_type in ('agent', 'group')", name="ck_schedules_target_type"
        ),
    )
    # Partial index: the scan only ever looks at enabled rules that are due.
    op.create_index(
        "idx_schedules_due",
        "schedules",
        ["next_run_at"],
        unique=False,
        postgresql_where=sa.text("enabled"),
    )
    op.create_index(
        "idx_schedules_owner", "schedules", ["owner_id", "created_at"], unique=False
    )

    op.create_table(
        "schedule_triggers",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "result",
            _enum(
                "schedule_trigger_result", "fired", "missed", "skipped_busy", "failed"
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("missed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "request_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "schedule_id", "planned_at", name="uq_schedule_triggers_planned"
        ),
        sa.CheckConstraint(
            "missed_count >= 0", name="ck_schedule_triggers_missed_count"
        ),
    )
    op.create_index(
        "idx_schedule_triggers_lookup",
        "schedule_triggers",
        ["schedule_id", "planned_at"],
        unique=False,
    )
    op.create_index(
        "idx_schedule_triggers_result",
        "schedule_triggers",
        ["schedule_id", "result", "planned_at"],
        unique=False,
    )

    op.add_column(
        "requests",
        sa.Column("source_schedule_id", sa.Uuid(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "requests_source_schedule_id_fkey",
        "requests",
        "schedules",
        ["source_schedule_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_requests_source_schedule_id",
        "requests",
        ["source_schedule_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_requests_source_schedule_id", table_name="requests")
    op.drop_constraint(
        "requests_source_schedule_id_fkey", "requests", type_="foreignkey"
    )
    op.drop_column("requests", "source_schedule_id")

    op.drop_index("idx_schedule_triggers_result", table_name="schedule_triggers")
    op.drop_index("idx_schedule_triggers_lookup", table_name="schedule_triggers")
    op.drop_table("schedule_triggers")

    op.drop_index("idx_schedules_owner", table_name="schedules")
    op.drop_index("idx_schedules_due", table_name="schedules")
    op.drop_table("schedules")

    for name, _values in _ENUM_DEFINITIONS:
        postgresql.ENUM(name=name).drop(op.get_bind(), checkfirst=True)
