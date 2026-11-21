"""departments + users.department_id (020)

Two changes, both additive:

- ``departments``: a self-referencing tree with a materialized ``path``
  (``/root/child/``) so "subtree of X" is one indexed ``LIKE`` prefix scan
  instead of a recursive CTE. Moving a department rewrites descendants' paths,
  which is fine — reorganisation is rare, reads are constant.
- ``users.department_id``: nullable, ``ON DELETE SET NULL`` — deleting a
  department must never delete people.

``assign_scope`` defaults to ``all`` and users without a department are treated
as ``all``, so upgrading changes **no** existing behaviour. Tightening happens
only after an admin builds the tree and sets scopes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_departments"
down_revision: str | Sequence[str] | None = "0019_project_member_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "parent_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("departments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "assign_scope",
            sa.String(length=20),
            nullable=False,
            server_default="all",
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
        sa.UniqueConstraint("parent_id", "name", name="uq_departments_parent_name"),
        sa.CheckConstraint(
            "assign_scope IN ('all', 'subtree', 'self')",
            name="ck_departments_assign_scope",
        ),
    )
    op.create_index("ix_departments_path", "departments", ["path"])

    op.add_column(
        "users",
        sa.Column(
            "department_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_department_id", "users", ["department_id"])


def downgrade() -> None:
    op.drop_index("ix_users_department_id", table_name="users")
    op.drop_column("users", "department_id")
    op.drop_index("ix_departments_path", table_name="departments")
    op.drop_table("departments")
