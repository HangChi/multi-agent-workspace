"""需求模型：看板卡片。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class Request(Base):
    """需求卡片：标题/描述/期望产出/优先级/附件。"""

    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
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

    owner = relationship("User", back_populates="requests")
    runs = relationship("Run", back_populates="request")
    source_schedule = relationship("Schedule", foreign_keys=[source_schedule_id])
