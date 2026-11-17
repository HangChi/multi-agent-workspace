"""组-成员关联模型。"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class GroupMember(Base):
    """组内成员（有序）。"""

    __tablename__ = "group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "agent_id", name="uq_group_agent"),
        UniqueConstraint("group_id", "position", name="uq_group_position"),
    )

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    group_id: Mapped[str] = mapped_column(ForeignKey("agent_groups.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    group = relationship("AgentGroup", back_populates="members")
    agent = relationship("Agent", back_populates="memberships")
