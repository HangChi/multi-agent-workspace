"""出站策略与访问记录。

两张表分别回答两个问题：
- `egress_rules`：**允许/禁止去哪**（策略，人工维护，数量少）
- `egress_accesses`：**实际去了哪**（事实，系统写入，量大）

求值顺序见 `app/executors/egress.py`：内置黑名单 → 全局 deny → (restricted) agent allow
→ default allow。任何 Agent 配置都不能放宽全局黑名单（FR-022）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class EgressRule(Base):
    """出站规则：一条要么是全局黑名单项，要么是某 Agent 的允许项。

    scope/action 的组合受约束（data-model 不变量 4）：
    - `scope='global'` → `agent_id IS NULL` 且 `action='deny'`
    - `scope='agent'`  → `agent_id` 必填且 `action='allow'`

    这条约束在 DB 层用 CHECK 表达，避免应用层漏校验写出「全局 allow」这种
    会静默放宽策略的行。
    """

    __tablename__ = "egress_rules"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "agent_id",
            "action",
            "target_type",
            "target",
            "port_range",
            name="uq_egress_rule",
        ),
        Index("ix_egress_rules_scope_agent", "scope", "agent_id"),
        Index("ix_egress_rules_enabled", "enabled"),
        # scope/action/agent_id 三者的一致性，用 CHECK 硬约束：
        # 全局规则只能是 deny 且无归属 Agent；Agent 规则只能是 allow 且必须归属。
        # 少了这条，「全局 allow」这种会静默放宽全平台策略的行就能写进去。
        CheckConstraint(
            "(scope = 'global' AND agent_id IS NULL AND action = 'deny')"
            " OR (scope = 'agent' AND agent_id IS NOT NULL AND action = 'allow')",
            name="ck_egress_rule_scope_action",
        ),
        # 注意：port_range 可为 NULL，PG 的 UNIQUE 视 NULL 互不相等，
        # 因此「同目标不同端口为空」的两条规则可能重复插入——应用层插入前需查重。
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_uuid)
    scope: Mapped[str] = mapped_column(
        Enum("global", "agent", name="egress_scope"), nullable=False
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    )
    action: Mapped[str] = mapped_column(Enum("deny", "allow", name="egress_action"), nullable=False)
    target_type: Mapped[str] = mapped_column(
        Enum("domain", "cidr", "ip", name="egress_target_type"), nullable=False
    )
    # domain 支持 `*.example.com` 通配；cidr 形如 `10.0.0.0/8`；ip 为单个地址
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    # 空 = 全部端口；`443` / `8000-8100`
    port_range: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    agent = relationship("Agent")


class EgressAccess(Base):
    """一次对外连接的事实记录。

    `target_ip` 用 PG 的 INET 类型而非字符串：后续要按网段做统计
    （`WHERE target_ip << '10.0.0.0/8'`），字符串比较做不到。

    写入方式为执行结束时**按批插入**（一次执行通常几十条），不做逐条实时写，
    避免高频写放大（data-model D-3）。
    """

    __tablename__ = "egress_accesses"
    __table_args__ = (
        Index("ix_egress_accesses_run", "run_id"),
        Index("ix_egress_accesses_agent_time", "agent_id", "occurred_at"),
        Index("ix_egress_accesses_action_time", "action", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    # 非沙箱场景（如 read_url）也可记录，故可为 NULL
    execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("sandbox_executions.id", ondelete="SET NULL"), nullable=True
    )
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)

    target_ip: Mapped[str] = mapped_column(INET, nullable=False)
    # `ip-direct` 表示直连 IP 未反查到域名（data-model 不变量 7）
    target_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[str] = mapped_column(
        String(10), nullable=False, default="tcp", server_default="tcp"
    )
    action: Mapped[str] = mapped_column(Enum("allow", "deny", name="egress_access_action"), nullable=False)
    # deny 时必填：要能说清「被哪条规则拦的」
    matched_rule_id: Mapped[str | None] = mapped_column(
        ForeignKey("egress_rules.id", ondelete="SET NULL"), nullable=True
    )
    bytes_out: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run = relationship("Run")
    agent = relationship("Agent")
