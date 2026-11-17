"""定时调度模型（004）：规则与触发记录。

两个实体，职责严格分离：

- `Schedule`：**规则**。可增删改，代表"以后要按这个来"。
- `ScheduleTrigger`：**事实**。一次性写入、不再修改，代表"这一刻发生了什么"。

分离的原因：规则会被用户反复修改，而"某天 9 点到底跑没跑"是不能被后续
修改抹掉的事实。如果把触发结果塞进规则行（比如只留 `last_result`），
用户改一次规则就会丢失全部历史，排障时无从下手。

幂等的落点也在触发记录上：`UNIQUE(schedule_id, planned_at)` 是「同一计划
时刻最多建一张单」（FR-012）的唯一硬保证。不用 Redis 锁 —— 锁有 TTL、
会被 flush，不能作为长期事实（见 research R-03）。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
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


class Schedule(Base):
    """一条周期性触发规则。

    时间字段一律 timestamptz（UTC 存储）；`cron_expr` 按 `timezone` 求值。
    """

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 周期表达式与求值时区。存字符串而不是解析后的结构：表达式是用户输入
    # 的原始意图，改版/换解析器后仍应可完整还原并重新校验。
    cron_expr: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Asia/Shanghai"
    )

    # ===== 需求模板（触发时据此建单）=====
    # 实测现有 RequestCreate 只接受这四项，requests 表也没有 deadline 列，
    # 因此模板只含这四项（见 data-model.md 的"不做哪些变更"）。
    template_title: Mapped[str] = mapped_column(String(200), nullable=False)
    template_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_priority: Mapped[str] = mapped_column(
        # 复用 0001 已创建的 request_priority 类型。
        # 模型层保留默认 create_type（与 request.py 的同名声明一致，
        # create_all 靠 checkfirst 去重）；迁移层才写 create_type=False，
        # 因为那里的类型由 0001 建好了。
        Enum("high", "normal", "low", name="request_priority"),
        nullable=False,
        server_default="normal",
    )

    # ===== 执行目标 =====
    # 多态（agent / group）无法用单个外键表达，因此不建外键，改由应用层
    # 在保存时与每次触发时校验（FR-023 要求按创建者当下的权限重新校验，
    # 本来就不能依赖数据库外键的静态保证）。
    target_type: Mapped[str] = mapped_column(
        Enum("agent", "group", name="schedule_target_type"), nullable=False
    )
    target_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    target_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ===== 状态 =====
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 冗余最近结果，让列表页无需联查触发记录表
    last_result: Mapped[str | None] = mapped_column(
        Enum(
            "fired",
            "missed",
            "skipped_busy",
            "failed",
            name="schedule_trigger_result",
        ),
        nullable=True,
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    auto_disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    triggers = relationship(
        "ScheduleTrigger",
        back_populates="schedule",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    owner = relationship("User")

    __table_args__ = (
        # 空白规则无意义
        CheckConstraint("name <> ''", name="ck_schedules_name_not_blank"),
        CheckConstraint("cron_expr <> ''", name="ck_schedules_cron_not_blank"),
        CheckConstraint(
            "template_title <> ''", name="ck_schedules_template_title_not_blank"
        ),
        # 启用态必须有下次触发时间。没有这条约束，一个"启用了但没有下次时间"
        # 的规则会永远不被扫描命中，表现为"规则好像生效了但从不执行"，
        # 而这种状态在界面上看起来完全正常。
        CheckConstraint(
            "not enabled or next_run_at is not null",
            name="ck_schedules_enabled_requires_next_run",
        ),
        CheckConstraint(
            "consecutive_failures >= 0", name="ck_schedules_failures_non_negative"
        ),
        CheckConstraint(
            "target_type in ('agent', 'group')", name="ck_schedules_target_type"
        ),
        # 扫描主查询路径：只扫启用中的到期规则（部分索引，避免全表扫）
        Index(
            "idx_schedules_due",
            "next_run_at",
            postgresql_where=text("enabled"),
        ),
        # 成员列表页：按创建时间倒序取自己的规则。
        # 复合索引已覆盖 owner_id 单列查询，无需再建单列索引。
        Index("idx_schedules_owner", "owner_id", "created_at"),
    )


class ScheduleTrigger(Base):
    """一次触发处理的可审计事实。写入后不再修改（错过记录的次数在写入时确定）。"""

    __tablename__ = "schedule_triggers"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False
    )
    # 本次处理对应的计划时刻。与 schedule_id 组成唯一键 —— 幂等基石。
    planned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # 实际处理时刻；错过记录没有实际处理（它只是被告知"没跑"），为 NULL
    fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result: Mapped[str] = mapped_column(
        Enum(
            "fired",
            "missed",
            "skipped_busy",
            "failed",
            name="schedule_trigger_result",
        ),
        nullable=False,
    )
    # 面向用户的中文短句，措辞白名单见 contracts/api.md §2
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 合并错过记录时的错过次数（仅 result='missed' 时 > 0）
    missed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # SET NULL 而非 CASCADE：需求被删（当前无此路径）不应连带抹掉"曾触发过"的事实
    request_id: Mapped[str | None] = mapped_column(
        ForeignKey("requests.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    schedule = relationship("Schedule", back_populates="triggers")

    __table_args__ = (
        # ★ 幂等基石：同一计划时刻最多一条记录，也就最多一张需求单
        UniqueConstraint(
            "schedule_id", "planned_at", name="uq_schedule_triggers_planned"
        ),
        # 历史列表主查询（倒序）
        Index(
            "idx_schedule_triggers_lookup",
            "schedule_id",
            "planned_at",
        ),
        # 按结果过滤 + counts 聚合
        Index(
            "idx_schedule_triggers_result",
            "schedule_id",
            "result",
            "planned_at",
        ),
        CheckConstraint("missed_count >= 0", name="ck_schedule_triggers_missed_count"),
    )
