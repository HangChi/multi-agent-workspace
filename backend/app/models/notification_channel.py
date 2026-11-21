"""外部通知通道（018）：用户配置的 webhook + 每次投递的记录。

两张表刻意分开：
- `notification_channels` 是**配置**（用户改它）；
- `notification_deliveries` 是**过程**（每次尝试都会更新状态）。

为什么不把投递状态写进通知的 payload：通知是「发生了什么」的不可变事实
（007/017 的纪律），投递是「我们尝试送达」的可变过程，生命周期与写入方都不同
（research R-09）。混在一起会让每次重试都去 UPDATE 一条历史事实。

secret：服务端生成（32 字节 urlsafe），只在算 HMAC 时读出来，**不回显、不写日志**
（research R-08）。不做加密存储的理由也写在那里。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
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

DELIVERY_PENDING = "pending"
DELIVERY_SUCCEEDED = "succeeded"
DELIVERY_FAILED = "failed"


class NotificationChannel(Base):
    """一条外部通道（本期仅 webhook）。"""

    __tablename__ = "notification_channels"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 本期只有 webhook；留字符串列是为了加邮件时不必改表（不建枚举，
    # 新 kind 不需要迁移——这是 017 踩过枚举坑之后的取舍）。
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="webhook")
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # 用于 HMAC 签名的共享密钥；服务端生成，不回显。
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    # 订阅的事件类型列表；空列表 = 全部事件（新事件类型自动收到）。
    event_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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

    __table_args__ = (
        CheckConstraint("kind IN ('webhook')", name="ck_notification_channels_kind"),
        Index("ix_notification_channels_user_id", "user_id"),
    )

    deliveries = relationship(
        "NotificationDelivery",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class NotificationDelivery(Base):
    """一次投递的尝试记录（at-least-once，见 research R-04）。"""

    __tablename__ = "notification_deliveries"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
    )
    notification_id: Mapped[str | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), nullable=True
    )
    # 事件类型冗余一份：通知行被清理后，投递记录仍能说明「送的是什么」
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DELIVERY_PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 最后一次错误摘要（≤500 字符；完整响应体不落库——可能含对方敏感信息）
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="ck_notification_deliveries_status",
        ),
        # 设置页按通道倒序取最近投递
        Index(
            "ix_notification_deliveries_channel_created",
            "channel_id",
            "created_at",
        ),
        Index("ix_notification_deliveries_status", "status"),
    )
