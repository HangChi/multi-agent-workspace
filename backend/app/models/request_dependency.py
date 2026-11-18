"""需求依赖边模型（009）：有向「先后」结构。

语义：`request_id` 依赖 `dependency_id`（后者是前置，先做）。
命名刻意中性——表里存的是结构（谁等谁），不存叙事（「阻塞」是门禁
视角的说法，见 data-model §1）。

可增可删（用户确认「误连能改」），但不更新：改语义 = 删旧建新。
删除历史不单独留表——审计日志（request_dependency_add/remove）承担。
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class RequestDependency(Base):
    """一条依赖边：request_id 等 dependency_id 完成。"""

    __tablename__ = "request_dependencies"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    # 被依赖方（B：要等别人的那条）。删需求 → 它的边全消失（CASCADE）：
    # 门禁与反查都挂在 request_id 上，边比需求活得久没有意义。
    request_id: Mapped[str] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    # 前置（A：先做的那条）。同样 CASCADE——A 没了，「等 A」的边失去指代。
    # 注意：需求实际上不会被删（无 DELETE 端点，只有取消），这条级联是
    # 防御性的（用户删除才会走到）。
    dependency_id: Mapped[str] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    # 连边人（审计溯源：这条边是谁连的，删边时也要判他的权限）。
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # 重复边没有第二语义（spec US1 场景 2）；并发重连由约束兜底转 409。
        UniqueConstraint(
            "request_id", "dependency_id", name="uq_request_dependencies_pair"
        ),
        # 库层堵死自依赖：service 先拦（400 友好错误），这里是兜底。
        # 双 FK 同表时，CHECK 是唯一能防呆的库级手段。
        CheckConstraint("request_id <> dependency_id", name="ck_no_self_dependency"),
        # 门禁查询路径（assign 热路径，SC-003 的 RT 预算靠它）。
        Index("ix_request_dependencies_request_id", "request_id"),
        # 反查路径（settle 解锁通知：「谁在等我刚收敛的这条」）。
        Index("ix_request_dependencies_dependency_id", "dependency_id"),
    )

    # 两侧关系（Request 上两个 relationship 的另一半）。必须放在列定义之后——
    # foreign_keys 引用类属性，类体执行时列还没存在会 NameError。
    request = relationship(
        "Request", foreign_keys=[request_id], back_populates="dependency_edges"
    )
    dependency = relationship(
        "Request", foreign_keys=[dependency_id], back_populates="dependent_edges"
    )
