"""Invite codes: revoked_at (015 优化).

为什么加这一列：邀请码此前只有「用尽」与「过期」两种失效方式，没有「吊销」。
实际后果是——邀请码一旦泄露或发错人，管理员只能等它用尽、等它过期、或者
直接改库；而 `/admin` 下根本没有邀请码管理端点，发新码同样只能改库
（2026-11-19 实测：默认码 10 次配额用尽后注册直接 400，只能手写 SQL 续发）。

``revoked_at`` 为空表示有效；非空表示已吊销。register 的校验与「原子消耗」
的 UPDATE 都带上 ``revoked_at IS NULL``，避免「吊销瞬间正好有并发注册」。

不删行：``users.invite_code_id`` 引用它，删了会断掉注册来源的审计链。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_invite_code_revoked"
down_revision: str | Sequence[str] | None = "0015_request_board_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invite_codes",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invite_codes", "revoked_at")
