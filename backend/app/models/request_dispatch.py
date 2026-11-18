"""分派历史模型（007）：append-only 经手链。

一条需求每次「分派/转交/接收/退回」产生一条不可变记录。只插入不更新不删除
（同 004 调度触发历史的纪律）：「谁经手过、为什么退」是审计的一等公民，
不是 audit_logs 全局动作流里顺带可查的副产品（research R-08）。

被否决的方案：requests.dispatch_history JSON 列——「按经手人找我参与的
需求」要扫全表 jsonb；只写 audit_logs——经手链要按 target_id 过滤再排序，
语义上降级为顺带。
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class RequestDispatch(Base):
    """一条分派动作记录。"""

    __tablename__ = "request_dispatches"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    # 索引在 __table_args__（列上 index=True 会重名冲突，同 Notification 的处理）。
    request_id: Mapped[str] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(
        Enum("dispatch", "reassign", "accept", "reject", name="dispatch_action"),
        nullable=False,
    )
    # 操作人。与 from/to 接收人都是 users.id；用户删除后历史行保留
    # （actor_id 不用 FK 级联删除——历史是事实，不随账号消失）。
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    from_assignee_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    to_assignee_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 每需求单调序号（1,2,3…）。为什么不能只按 created_at 排序：
    # PG 的 now() 是**事务开始时间**，行锁等待会让「后插入」的事务持有
    # 更早的时间戳（它先开始、只是后拿到锁）——并发下 created_at 会颠倒，
    # 而经手链是叙事顺序，颠倒就说反了「谁先谁后」。seq 在请求行锁保护下
    # 取 max+1，与锁串行化天然一致。
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request = relationship("Request", back_populates="dispatches")

    # 经手链查询（详情页时间线，按 request_id + seq 正序）。与迁移 0011 一致。
    __table_args__ = (
        Index("ix_request_dispatches_request_id", "request_id"),
        Index("ix_request_dispatches_request_seq", "request_id", "seq"),
    )
