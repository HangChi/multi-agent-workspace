"""Add agent templates: a snapshot table plus two nullable source columns on agents.

Three things are added, in dependency order:

1. ``agent_templates``: a snapshot of the ten Agent configuration fields. It has no
   ``status``, no runs and no group memberships — it is "configuration waiting to be
   instantiated", not "an Agent that cannot run". That is why it is a separate table
   instead of an ``is_template`` flag on ``agents`` (see plan.md, Complexity Tracking).
2. Two nullable columns on ``agents`` — ``source_template_id`` and
   ``source_agent_id`` — both ``ON DELETE SET NULL``. Deleting a template or a source
   Agent must never delete the Agents already produced from them (FR-021). The
   self-referencing foreign key is safe: SET NULL does not trigger a delete action on
   the referenced row.
3. A **partial unique index** on ``agent_templates(name) WHERE owner_id IS NULL``.
   This is the whole reason the migration exists as more than a table: a plain
   ``UNIQUE(owner_id, name)`` does not constrain rows whose ``owner_id`` is NULL,
   because NULL is never equal to NULL in PostgreSQL. Built-in templates all have a
   NULL owner, so without the partial index the seed script would insert duplicate
   built-ins on every run and only fail in production.

``egress_mode`` reuses the enum created by 002 (``create_type=False``), exactly like
0007 reuses ``request_priority``. The enum types are not dropped on downgrade: they
belong to 002.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_agent_templates"
down_revision: str | Sequence[str] | None = "0008_run_cost_micros"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_templates",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column(
            "temperature",
            sa.Float(),
            nullable=False,
            server_default="0.7",
        ),
        sa.Column("max_steps", sa.Integer(), nullable=False, server_default="10"),
        sa.Column(
            "max_run_seconds", sa.Integer(), nullable=False, server_default="1800"
        ),
        sa.Column("tools", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "cost_limit_cents", sa.Integer(), nullable=False, server_default="10000"
        ),
        # Reuses the enum created by 002; create_type=False keeps Alembic from
        # emitting a second CREATE TYPE for it (the same technique 0007 uses to
        # reuse request_priority).
        sa.Column(
            "egress_mode",
            postgresql.ENUM(
                "default", "restricted", name="agent_egress_mode", create_type=False
            ),
            nullable=False,
            server_default="default",
        ),
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
        # Blank content is meaningless.
        sa.CheckConstraint("name <> ''", name="ck_agent_templates_name_not_blank"),
        sa.CheckConstraint(
            "system_prompt <> ''", name="ck_agent_templates_prompt_not_blank"
        ),
        # Field-for-field the same invariants as `agents` (FR-022): a template must
        # never be a way around Agent validation.
        sa.CheckConstraint(
            "temperature BETWEEN 0.0 AND 2.0",
            name="ck_agent_templates_temperature_range",
        ),
        sa.CheckConstraint(
            "max_steps BETWEEN 1 AND 100", name="ck_agent_templates_max_steps_range"
        ),
        sa.CheckConstraint(
            "max_run_seconds BETWEEN 1 AND 86400",
            name="ck_agent_templates_max_run_seconds_range",
        ),
        sa.CheckConstraint(
            "cost_limit_cents >= 1", name="ck_agent_templates_cost_limit_positive"
        ),
        # Member-owned templates: unique per owner. Note this does NOT constrain
        # NULL owners — see the partial index below.
        sa.UniqueConstraint("owner_id", "name", name="uq_agent_templates_owner_name"),
    )
    op.create_index(
        "idx_agent_templates_owner", "agent_templates", ["owner_id", "created_at"]
    )
    # ★ The built-in uniqueness guarantee. Without it, seeding is not idempotent.
    op.create_index(
        "uq_agent_templates_builtin_name",
        "agent_templates",
        ["name"],
        unique=True,
        postgresql_where=sa.text("owner_id IS NULL"),
    )

    op.add_column(
        "agents", sa.Column("source_template_id", sa.Uuid(as_uuid=False), nullable=True)
    )
    op.create_foreign_key(
        "agents_source_template_id_fkey",
        "agents",
        "agent_templates",
        ["source_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agents_source_template_id", "agents", ["source_template_id"], unique=False
    )

    op.add_column(
        "agents", sa.Column("source_agent_id", sa.Uuid(as_uuid=False), nullable=True)
    )
    # Self-referencing foreign key on the same table.
    op.create_foreign_key(
        "agents_source_agent_id_fkey",
        "agents",
        "agents",
        ["source_agent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agents_source_agent_id", "agents", ["source_agent_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_agents_source_agent_id", table_name="agents")
    op.drop_constraint("agents_source_agent_id_fkey", "agents", type_="foreignkey")
    op.drop_column("agents", "source_agent_id")

    op.drop_index("ix_agents_source_template_id", table_name="agents")
    op.drop_constraint("agents_source_template_id_fkey", "agents", type_="foreignkey")
    op.drop_column("agents", "source_template_id")

    op.drop_index("uq_agent_templates_builtin_name", table_name="agent_templates")
    op.drop_index("idx_agent_templates_owner", table_name="agent_templates")
    op.drop_table("agent_templates")
