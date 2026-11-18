"""Project members: project_members table + two notification types (010).

One new table: ``(project_id, user_id)`` composite-primary-key membership
rows — no ``role`` column (membership *is* the role; the owner lives in
``projects.owner_id``, research R-01). Two new ``notification_type`` enum
values for member add/remove (only sent to the affected user; deleting a
project sends nothing — research R-05).

The enum values are added in an autocommit block for the same reason as
0011/0013: PostgreSQL allows ``ADD VALUE`` inside a transaction, but the
new value cannot be *used* in that same transaction. Downgrade drops the
table but keeps the enum values (an old worker may still reference them).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014_project_member"
down_revision: str | Sequence[str] | None = "0013_request_dependency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_members",
        # 复合主键即唯一约束：成员关系无第二行，同名并发加入由 PK 兜底转 409。
        sa.Column(
            "project_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # 「我是哪些项目的成员」反查——relevant 口径的热路径。
    op.create_index(
        "ix_project_members_user_id", "project_members", ["user_id"]
    )
    # 枚举新值（autocommit_block：同事务不能使用新值，同 0011/0013）。
    # 只发给对方本人的成员变动通知；删项目不发（点进 404 是噪声，R-05）。
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS "
            "'project_member_added'"
        )
        op.execute(
            "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS "
            "'project_member_removed'"
        )


def downgrade() -> None:
    # 枚举值不删：见模块 docstring（同 0011/0013 纪律）。
    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_table("project_members")
