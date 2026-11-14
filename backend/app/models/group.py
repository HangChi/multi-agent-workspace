"""Agent 组模型。"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class AgentGroup(Base):
    """Agent 组：有序成员 + 默认路由策略。"""

    __tablename__ = "agent_groups"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    routing_strategy: Mapped[str] = mapped_column(
        Enum("direct", "fan_out", "pipeline", "router", name="group_routing"), nullable=False, default="fan_out"
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "archived", name="group_status"), nullable=False, default="active"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    owner = relationship("User", back_populates="groups")
    members = relationship(
        "GroupMember",
        back_populates="group",
        cascade="all, delete-orphan",
        order_by="GroupMember.position",
    )
