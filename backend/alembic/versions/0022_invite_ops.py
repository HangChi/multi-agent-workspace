"""invite operations: department quota + code attribution (022)

- ``departments.invite_quota``: monthly seat budget (sum of ``max_uses``) per
  department. NULL = unlimited, so existing deployments keep working.
- ``invite_codes.department_id``: which department a code recruits for. Needed
  by the quota check and by「这个部门今年招了几个人」.

Why quota lives on the department: hiring budget is per-department. A global
cap just lets whoever asks first exhaust it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_invite_ops"
down_revision: str | Sequence[str] | None = "0021_request_auto_start"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("departments", sa.Column("invite_quota", sa.Integer(), nullable=True))
    op.add_column(
        "invite_codes",
        sa.Column(
            "department_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_invite_codes_department_id", "invite_codes", ["department_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_invite_codes_department_id", table_name="invite_codes")
    op.drop_column("invite_codes", "department_id")
    op.drop_column("departments", "invite_quota")
