"""Agent 模型：可执行的执行单元。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class Agent(Base):
    """Agent：含角色提示词、模型、工具集、成本上限。"""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    max_run_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    tools: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 出站模式（002-sandboxed-tools）：
    # - `default`    放行并记录（有全局黑名单兜底）
    # - `restricted` 仅允许 egress_rules 中该 Agent 的 allow 项
    # 注意：无论哪种模式，全局黑名单都不可放宽（FR-022）。
    egress_mode: Mapped[str] = mapped_column(
        Enum("default", "restricted", name="agent_egress_mode"),
        nullable=False,
        default="default",
        server_default="default",
    )
    cost_limit_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10000
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "archived", name="agent_status"),
        nullable=False,
        default="active",
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

    owner = relationship("User", back_populates="agents")
    memberships = relationship(
        "GroupMember", back_populates="agent", cascade="all, delete-orphan"
    )
