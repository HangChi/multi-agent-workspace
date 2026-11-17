"""Agent 模板模型（005）：一段可复用的 Agent 配置快照。

与 `agents` 表的关系：10 个配置字段逐字同义（约束见 schemas/agent_template.py 的
反漂移单测），但模板**没有** `status`、没有运行、没有组引用 —— 它是「待实例化的配置」，
不是「一个不能跑的 Agent」。因此独立成表，而不是给 `agents` 加 `is_template` 列
（plan.md Complexity Tracking 第一条）。

内置模板用 `owner_id IS NULL` 表达，不引入 `is_builtin` 布尔列：
多一个布尔列就多一个可能与 owner_id 互相矛盾的状态源。

⚠️ 唯一性的坑（research R-05）：PostgreSQL 的唯一约束在含 NULL 时**不生效**
（NULL 与 NULL 不相等），所以 `UNIQUE(owner_id, name)` 对若干内置行完全不设防，
必须另建**部分唯一索引** `uq_agent_templates_builtin_name`。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class AgentTemplate(Base):
    """一段可复用的 Agent 配置快照。

    `owner_id` 为空 = 内置模板（所有成员可见、只读，由种子脚本注入）。
    """

    __tablename__ = "agent_templates"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    # 可空：内置模板没有所有者。级联删除与 schedules.owner_id 一致（用户没了，
    # 其私有模板不该留下）。
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ===== 10 个配置字段（与 agents 同义，见 schemas 的反漂移单测）=====
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    temperature: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.7, server_default="0.7"
    )
    max_steps: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    max_run_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1800, server_default="1800"
    )
    tools: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    cost_limit_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10000, server_default="10000"
    )
    # 出站模式复用 002 的 enum。模型层保留 create_type 默认（与 agent.py 一致），
    # 迁移层才写 create_type=False（类型由 002 建好）。
    egress_mode: Mapped[str] = mapped_column(
        Enum("default", "restricted", name="agent_egress_mode"),
        nullable=False,
        default="default",
        server_default="default",
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

    # 由本模板创建的 Agent（只用于反查/追溯，不参与业务逻辑）
    derived_agents = relationship(
        "Agent",
        primaryjoin="AgentTemplate.id == Agent.source_template_id",
        foreign_keys="Agent.source_template_id",
        viewonly=True,
    )
    owner = relationship("User", foreign_keys=[owner_id])

    __table_args__ = (
        # 空白模板无意义
        CheckConstraint("name <> ''", name="ck_agent_templates_name_not_blank"),
        CheckConstraint(
            "system_prompt <> ''", name="ck_agent_templates_prompt_not_blank"
        ),
        # 与 agents 的同名约束逐字对齐（FR-022：模板不得成为绕过校验的旁路）
        CheckConstraint(
            "temperature BETWEEN 0.0 AND 2.0",
            name="ck_agent_templates_temperature_range",
        ),
        CheckConstraint(
            "max_steps BETWEEN 1 AND 100", name="ck_agent_templates_max_steps_range"
        ),
        CheckConstraint(
            "max_run_seconds BETWEEN 1 AND 86400",
            name="ck_agent_templates_max_run_seconds_range",
        ),
        CheckConstraint(
            "cost_limit_cents >= 1", name="ck_agent_templates_cost_limit_positive"
        ),
        # 成员私有模板：同一所有者下名称唯一。
        # ⚠️ 对 owner_id IS NULL 的行无效（NULL 语义），内置唯一性靠下面的部分索引。
        UniqueConstraint("owner_id", "name", name="uq_agent_templates_owner_name"),
        # ★ 内置模板唯一性：部分索引，WHERE owner_id IS NULL。
        # 没有它，种子脚本重复执行会插入重名内置模板，而 SC-008 只在生产才失败。
        Index(
            "uq_agent_templates_builtin_name",
            "name",
            unique=True,
            postgresql_where=text("owner_id IS NULL"),
        ),
        # 成员列表主路径：按创建时间倒序取自己的。复合索引已覆盖 owner_id 单列查询。
        Index("idx_agent_templates_owner", "owner_id", "created_at"),
    )
