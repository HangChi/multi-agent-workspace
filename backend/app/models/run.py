"""运行模型：一次具体执行。"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class Run(Base):
    """一次执行：状态机载体，记录成本/结果/错误。"""

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_status_created", "status", "created_at"),
        Index("ix_runs_request", "request_id"),
        Index("ix_runs_parent", "parent_run_id"),
        Index("ix_runs_parent_sequence", "parent_run_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), nullable=False)
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    assignee_type: Mapped[str] = mapped_column(Enum("agent", "group", name="run_assignee_type"), nullable=False)
    assignee_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    strategy: Mapped[str] = mapped_column(
        Enum("direct", "fan_out", "pipeline", "router", name="run_strategy"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "queued",
            "running",
            "needs_input",
            "completed",
            "failed",
            "cancelled",
            "timeout",
            name="run_status",
        ),
        nullable=False,
        default="queued",
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # pipeline 策略下的阶段序号（1..N，按组内 position 顺序）；其他策略为 0
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # needs_input 挂起时的用户回复（FR-021）：reply 端点写入，恢复执行时注入对话上下文
    user_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    request = relationship("Request", back_populates="runs")
    owner = relationship("User", back_populates="runs")
    steps = relationship("Step", back_populates="run", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="run", cascade="all, delete-orphan")
    parent_run = relationship("Run", remote_side=[id], back_populates="child_runs")
    child_runs = relationship("Run", back_populates="parent_run")

    # ---- 状态机协议（state_machine.RunLike）----

    def mark_started(self) -> None:
        from datetime import datetime

        self.started_at = datetime.now(UTC)

    def mark_finished(self) -> None:
        from datetime import datetime

        self.finished_at = datetime.now(UTC)

    def clear_execution(self) -> None:
        """failed/timeout → queued 重试时重置执行现场（FR-017）。"""
        self.current_step = 0
        self.cost_cents = 0
        self.error = None
        self.result = None
        self.started_at = None
        self.finished_at = None
