"""template visibility + version history (023)

- ``agent_templates.visibility``: ``private`` (default) / ``team``. Team templates
  are usable by every member ("create from template") but only editable by the
  owner. Builtin templates (``owner_id IS NULL``) behave as ``team``.
- ``agent_template_versions``: full config snapshots. Snapshots, not diffs —
  replaying diffs to restore a version is one mistake away from a half-applied
  config, and a template is 10 scalar fields.

Default ``private`` means every existing row keeps its current reach (owner-only),
so this migration changes nobody's sharing state.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_template_versions"
down_revision: str | Sequence[str] | None = "0022_invite_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_templates",
        sa.Column(
            "visibility",
            sa.String(length=20),
            nullable=False,
            server_default="private",
        ),
    )
    op.create_check_constraint(
        "ck_agent_templates_visibility",
        "agent_templates",
        "visibility IN ('private', 'team')",
    )
    op.create_index(
        "idx_agent_templates_visibility",
        "agent_templates",
        ["visibility", "created_at"],
    )

    op.create_table(
        "agent_template_versions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "template_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("agent_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column(
            "created_by",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("template_id", "version", name="uq_template_versions_no"),
    )
    op.create_index(
        "idx_template_versions_template",
        "agent_template_versions",
        ["template_id", "version"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_template_versions_template", table_name="agent_template_versions"
    )
    op.drop_table("agent_template_versions")
    op.drop_index("idx_agent_templates_visibility", table_name="agent_templates")
    op.drop_constraint(
        "ck_agent_templates_visibility", "agent_templates", type_="check"
    )
    op.drop_column("agent_templates", "visibility")
