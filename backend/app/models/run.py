"""运行模型：一次具体执行。"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
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

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), nullable=False)
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id"), nullable=True
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    assignee_type: Mapped[str] = mapped_column(
        Enum("agent", "group", name="run_assignee_type"), nullable=False
    )
    assignee_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    strategy: Mapped[str] = mapped_column(
        Enum("direct", "fan_out", "pipeline", "router", name="run_strategy"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "queued",
            "running",
            "needs_input",
            "retrying",
            "completed",
            "failed",
            "cancelled",
            "timeout",
            name="run_status",
        ),
        nullable=False,
        default="queued",
    )
    # 006：第几次尝试（首次执行为 1，每次自动/手动重试 +1）
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # 006：下次重试的计划时刻；非 NULL 表示正在等待重试。
    # 与 status='retrying' 同真同假（冗余字段），用于 reaper 排除与前端倒计时。
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 006：历次失败原因 [{"attempt":1,"error":"...","at":"..."}]。
    # 与 error 分开：error 是「当前最后一次」，重试成功后要清；历史必须留（FR-017）。
    retry_history: Mapped[list] = mapped_column(
        JSON(none_as_null=True), nullable=False, default=list, server_default="[]"
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # pipeline 策略下的阶段序号（1..N，按组内 position 顺序）；其他策略为 0
    sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Assignment-time grants (router: first member selection). NULL means legacy
    # history was not recorded, not that the run had no tools. Never reset on retry.
    tool_grants_snapshot: Mapped[list[str] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # needs_input 挂起时的用户回复（FR-021）：reply 端点写入，恢复执行时注入对话上下文
    user_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 展示/配额用的整分；由 cost_micros 换算而来，不要直接累加
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 精确累计（1 分 = 1e6 微分）：单次 gpt-4o-mini 调用约 0.015 分，
    # 按分取整会记成 0，多步任务的总成本被系统性低估。
    cost_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    request = relationship("Request", back_populates="runs")
    owner = relationship("User", back_populates="runs")
    steps = relationship("Step", back_populates="run", cascade="all, delete-orphan")
    artifacts = relationship(
        "Artifact", back_populates="run", cascade="all, delete-orphan"
    )
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
        """failed/timeout → queued 手动重试时重置执行现场（FR-017）。

        006 起**不清零成本**（FR-013）：重试花的 token 是真实发生的支出，
        清零会让看板成本系统性低于网关账单、配额统计偏低，而且熔断可被
        「每次各花 490 分、上限 500 分」绕过（FR-014）。
        详见 specs/006-run-retry/research.md R-04。
        """
        self.current_step = 0
        self.error = None
        self.result = None
        self.started_at = None
        self.finished_at = None

    def bump_attempt(self) -> int:
        """递增尝试计数并返回新值（自动与手动重试共用）。"""
        self.attempt = (self.attempt or 1) + 1
        return self.attempt

    def record_failure(self, error: str) -> None:
        """把一次失败追加进 retry_history（FR-017：每次尝试的原因可查）。"""
        from datetime import datetime

        history = list(self.retry_history or [])
        history.append(
            {
                "attempt": self.attempt or 1,
                "error": error,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        self.retry_history = history
