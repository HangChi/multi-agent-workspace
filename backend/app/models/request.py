"""需求模型：看板卡片 + 分派责任指针（007）。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class Request(Base):
    """需求卡片：标题/描述/期望产出/优先级/附件 + 分派指针（007）。

    分派三列是不变量的一部分（state_machine.assert_dispatch_invariants）：
    ``assignee_id`` 非空 ⇔ 状态 ∈ {pending, accepted}；``accepted_at`` 非空
    ⇔ 状态 = accepted。``assignee_id`` 用 ON DELETE SET NULL —— 用户被删时
    需求本体不随之消失（「需求是已经发生过的事」，同 source_schedule_id 纪律），
    经手历史在 request_dispatches 表追溯，本表只存当前责任指针。
    """

    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    # 007：当前接收人（分派给员工的责任指针）。NULL = 无人认领。
    # 不带 index=True：复合索引 ix_requests_assignee_status 已覆盖其前缀，
    # 单列索引是纯冗余（同 Notification 的处理，索引只在 __table_args__ 声明）。
    assignee_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(
        Enum("high", "normal", "low", name="request_priority"),
        nullable=False,
        default="normal",
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "created",
            "pending",
            "accepted",
            "assigned",
            "running",
            "completed",
            "failed",
            "cancelled",
            name="request_status",
        ),
        nullable=False,
        default="created",
    )
    # 007：最近一次分派/转交时间与当前接收人接收时间。退回时随 assignee 一并清空。
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attachments: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 来源调度（004）：可空。手工创建的需求为 NULL。
    # 用 ON DELETE SET NULL 而不是 CASCADE —— 删除调度规则绝不能连带删除它
    # 已经产生的需求（进而连带 Run、Step、制品）。规则是"以后怎么触发"，
    # 需求是"已经发生过的事情"，后者不因前者被撤销而消失。
    source_schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner = relationship("User", back_populates="requests", foreign_keys=[owner_id])
    assignee = relationship("User", foreign_keys=[assignee_id])
    runs = relationship("Run", back_populates="request")
    source_schedule = relationship("Schedule", foreign_keys=[source_schedule_id])
    dispatches = relationship(
        "RequestDispatch",
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    notifications = relationship(
        "Notification",
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # 收件箱热点查询（分派给我且 pending/accepted）的覆盖索引。
    # 与迁移 0011 声明一致——测试库走 create_all，索引必须声明在模型侧，
    # 否则「迁移里有、测试里没有」会让 prod/test 静默分叉。
    __table_args__ = (Index("ix_requests_assignee_status", "assignee_id", "status"),)
