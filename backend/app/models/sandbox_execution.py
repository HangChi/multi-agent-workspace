"""沙箱执行记录：一次脚本执行的事实。

与 `steps` 1:1（`step_id` UNIQUE）。失败分类直接落库（`status` + `error_category`），
这是 SC-008「可区分失败原因」的数据基础——不能只留一个 "失败"。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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


class SandboxExecution(Base):
    """一次沙箱执行。

    `code` / `stdout` / `stderr` 会按保留策略（90 天）清空，只留元数据与指纹，
    避免表无限膨胀；清空后仍可按 `code_sha256` 做复现比对。
    """

    __tablename__ = "sandbox_executions"
    __table_args__ = (
        Index("ix_sandbox_executions_run", "run_id"),
        Index("ix_sandbox_executions_agent_created", "agent_id", "created_at"),
        Index("ix_sandbox_executions_status", "status"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    # UNIQUE：一次执行只对应一个步骤，一个步骤最多一次执行（data-model 不变量 1）
    step_id: Mapped[str] = mapped_column(ForeignKey("steps.id"), nullable=False, unique=True)
    # 冗余：供按 Agent 统计与配额核算，避免每次 join steps→runs→agents
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)

    language: Mapped[str] = mapped_column(
        Enum("python", "shell", name="sandbox_language"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    code_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        Enum(
            "running",
            "ok",
            "failed",
            "timeout",
            "resource",
            "policy",
            "infra",
            name="sandbox_status",
        ),
        nullable=False,
        default="running",
        server_default="running",
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    stdout_truncated: Mapped[bool] = mapped_column(
        # 默认 False；截断时置 True 并保留原始长度，界面才能提示「已截断」
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    stdout_full_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 资源峰值：从 cgroup 读，读不到为 NULL（不编造 0，否则监控看不出「没测到」）
    cpu_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mem_peak_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    image: Mapped[str] = mapped_column(String(120), nullable=False)
    # {timeout_s, cpu, mem_mb, disk_mb, pids} 快照：事后追溯「当时给了多少资源」
    limits: Mapped[dict] = mapped_column(JSON, nullable=False)

    error_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run = relationship("Run")
    step = relationship("Step")
    agent = relationship("Agent")
