"""站内通知模型（007）：发给某用户的个人消息。

title 存**标题快照**而非只存 request_id JOIN——需求标题可改，通知是
「当时发生了什么」的记录（同分派历史的不可变纪律，data-model §4）。

未读查询用部分索引 (user_id, created_at DESC) WHERE read_at IS NULL：
已读行不入索引，控制体积；未读计数 COUNT 足够，不物化 unread_count
（多一个要维护一致性的写入点，research R-10）。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class Notification(Base):
    """一条站内通知。"""

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    # 索引统一在 __table_args__ 声明：列上的 index=True 会按约定名再建一个
    # ix_notifications_user_id，与显式索引重名（create_all 直接 DuplicateTable）。
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        Enum(
            "request_dispatched",
            "request_accepted",
            "request_rejected",
            "request_reassigned",
            "run_completed",
            # 009：解锁通知（前置终态 → 后续方「可以启动了」）
            "dependency_ready",
            # 010：成员变动（加人/移出——只发给对方本人；删项目不发，R-05）
            "project_member_added",
            "project_member_removed",
            name="notification_type",
        ),
        nullable=False,
    )
    request_id: Mapped[str | None] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # NULL = 未读。已读时间是个人读状态，不影响通知内容本身。
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    request = relationship("Request", back_populates="notifications")

    # 通知列表主索引 + 未读部分索引（已读行不入索引，控制体积）。
    # 与迁移 0011 一致——测试库走 create_all，声明必须在模型侧。
    __table_args__ = (
        Index("ix_notifications_user_id", "user_id"),
        Index(
            "ix_notifications_user_unread",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("read_at IS NULL"),
        ),
    )
