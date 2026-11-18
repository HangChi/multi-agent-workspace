"""Request dispatch: assignee pointer, dispatch history, notifications (007).

Three nullable columns on ``requests`` (assignee_id / dispatched_at /
accepted_at), two new tables (``request_dispatches`` append-only,
``notifications``), two new values on the ``request_status`` enum
(``pending`` / ``accepted``), and the assignee-status partial covering index
for the inbox query.

The enum values are added in an autocommit block for the same reason as 0010:
PostgreSQL allows ``ADD VALUE`` inside a transaction, but the new value cannot
be *used* in that same transaction. No data backfill is needed — every new
column is nullable and no existing row is a dispatch participant.

Downgrade drops the columns and tables but keeps the enum values (PostgreSQL
cannot safely drop them and an old worker may still reference them, same
discipline as 0010).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_request_dispatch"
down_revision: str | Sequence[str] | None = "0010_run_retry_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. requests 三个新列（全可空：存量需求无人认领，默认行为不变）。
    op.add_column(
        "requests",
        sa.Column(
            "assignee_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "requests",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "requests",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_requests_assignee_status",
        "requests",
        ["assignee_id", "status"],
    )

    # 2. request_dispatches：append-only 经手链。
    op.create_table(
        "request_dispatches",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "request_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "action",
            sa.Enum(
                "dispatch",
                "reassign",
                "accept",
                "reject",
                name="dispatch_action",
            ),
            nullable=False,
        ),
        # actor_id 不加 FK：历史是事实，不随账号删除而消失。
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column(
            "from_assignee_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "to_assignee_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_request_dispatches_request_id",
        "request_dispatches",
        ["request_id"],
    )
    # 经手链按 (request_id, seq) 正序：seq 是每需求单调序号，created_at 的
    # now() 是事务时间、行锁下会颠倒，不能作排序键（见模型注释）。
    op.create_index(
        "ix_request_dispatches_request_seq",
        "request_dispatches",
        ["request_id", "seq"],
    )

    # 3. notifications：站内通知，未读部分索引。
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "type",
            sa.Enum(
                "request_dispatched",
                "request_accepted",
                "request_rejected",
                "request_reassigned",
                "run_completed",
                name="notification_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "request_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("requests.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_notifications_user_id",
        "notifications",
        ["user_id"],
    )
    # 未读部分索引：已读行不入索引（data-model §4）。
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("read_at IS NULL"),
    )

    # 4. 枚举新值（autocommit_block：同事务不能使用新值，同 0010）。
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'pending' "
            "AFTER 'created'"
        )
        op.execute(
            "ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'accepted' "
            "AFTER 'pending'"
        )


def downgrade() -> None:
    # 枚举值不删：见模块 docstring。
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(
        "ix_request_dispatches_request_id", table_name="request_dispatches"
    )
    op.drop_table("request_dispatches")
    op.drop_index("ix_requests_assignee_status", table_name="requests")
    op.drop_column("requests", "accepted_at")
    op.drop_column("requests", "dispatched_at")
    op.drop_column("requests", "assignee_id")
