"""project member role (019)

Adds ``project_members.role`` with two values: ``editor`` (default, current
behaviour) and ``viewer`` (read-only member).

Why this is a *column* now, after 010 explicitly said "no role column": the
requirement changed. 010's reasoning was "membership *is* the role, and the
third state has no usage data" — that held while every member could do exactly
the same thing. A read-only member (stakeholder, auditor, someone from another
team who just needs visibility) is a real request, and faking it with "don't
add them" loses the visibility they need.

Scope stays deliberately small: **two** roles, no custom permission matrix.
The owner is still ``projects.owner_id`` and is not a row here (010 R-01).

``server_default='editor'`` keeps every existing row working unchanged —
migration is a pure widening, no data backfill needed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019_project_member_role"
down_revision: str | Sequence[str] | None = "0018_notification_channels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_members",
        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'editor'"),
        ),
    )
    op.create_check_constraint(
        "ck_project_members_role",
        "project_members",
        "role IN ('editor', 'viewer')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_project_members_role", "project_members", type_="check")
    op.drop_column("project_members", "role")
