"""invite requests (027)

One table: ``invite_requests``. Approving a request issues codes through the
existing 022 path (``create_invite_codes``), so the department quota check and
the "no half batch" rule apply unchanged — the approval flow adds process, not
a second way to mint codes.

``issued_codes`` stores the plain code values (newline separated) for the
requester to copy. Codes are not secrets in this system (015 already returns
them in the admin list; they can be revoked), so duplicating them here keeps
the requester's view simple.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_invite_requests"
down_revision: str | Sequence[str] | None = "0025_stdio_transport"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # notification_type 是 PG 枚举：加值必须单独一个 autocommit 块
    # （与 DDL 同事务时新值不能在同一事务里使用——0017 的教训）
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'invite_request_approved'"
        )
        op.execute(
            "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'invite_request_rejected'"
        )

    op.create_table(
        "invite_requests",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "requester_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "department_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "role", sa.String(length=20), nullable=False, server_default="member"
        ),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column(
            "decided_by",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_codes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_invite_requests_status",
        ),
        sa.CheckConstraint("count BETWEEN 1 AND 20", name="ck_invite_requests_count"),
        sa.CheckConstraint(
            "role IN ('member', 'viewer')", name="ck_invite_requests_role"
        ),
    )
    op.create_index(
        "idx_invite_requests_status", "invite_requests", ["status", "created_at"]
    )
    op.create_index(
        "idx_invite_requests_requester",
        "invite_requests",
        ["requester_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_invite_requests_requester", table_name="invite_requests")
    op.drop_index("idx_invite_requests_status", table_name="invite_requests")
    op.drop_table("invite_requests")
