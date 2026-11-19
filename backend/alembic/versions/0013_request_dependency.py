"""Request dependency edges: request_dependencies table (009).

One new table only: directed edges meaning "request_id waits for
dependency_id". No column changes on ``requests`` (the derived fields
live in the API layer, see data-model §2) and no state-machine changes
(the gate is an assign-time precondition, not a status — research R-03).

Downgrade drops the table. The downgrade **destroys the dependency
structure itself** (the only entity this feature owns); it is documented
in the quickstart as the rollback surface, same discipline as 007/008.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013_request_dependency"
down_revision: str | Sequence[str] | None = "0012_project_entity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_dependencies",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # 被依赖方（B：等别人的那条）。CASCADE：删需求时它的边全消失——
        # 门禁与反查都挂在 request_id 上，边比需求活得久没有意义。
        sa.Column(
            "request_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 前置（A：先做的那条）。同样 CASCADE（防御性：需求实际上只取消
        # 不删除，见模型注释）。
        sa.Column(
            "dependency_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 连边人（审计溯源 + 删边时的权限判定）。
        sa.Column(
            "created_by",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # 重复边没有第二语义（spec US1 场景 2）；并发重连由约束兜底转 409。
        sa.UniqueConstraint(
            "request_id", "dependency_id", name="uq_request_dependencies_pair"
        ),
        # 库层堵死自依赖：service 先拦（400），这里是兜底。双 FK 同表时
        # CHECK 是唯一能防呆的库级手段。
        sa.CheckConstraint("request_id <> dependency_id", name="ck_no_self_dependency"),
    )
    # 门禁查询路径（assign 热路径，SC-003 的 RT 预算靠它）
    op.create_index(
        "ix_request_dependencies_request_id",
        "request_dependencies",
        ["request_id"],
    )
    # 反查路径（settle 解锁通知）
    op.create_index(
        "ix_request_dependencies_dependency_id",
        "request_dependencies",
        ["dependency_id"],
    )
    # 枚举新值（autocommit_block：同事务不能使用新值，同 0011/0010）。
    # dependency_ready 是 009 解锁通知的类型；downgrade 不删枚举值
    # （PG 不能安全删，旧 worker 可能还在引用，同 0011 纪律）。
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'dependency_ready'"
        )


def downgrade() -> None:
    op.drop_index(
        "ix_request_dependencies_dependency_id", table_name="request_dependencies"
    )
    op.drop_index(
        "ix_request_dependencies_request_id", table_name="request_dependencies"
    )
    op.drop_table("request_dependencies")
