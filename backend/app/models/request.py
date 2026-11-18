"""需求模型：看板卡片 + 分派责任指针（007）。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.request_dependency import RequestDependency
from app.models.user import new_uuid


class Request(Base):
    """需求卡片：标题/描述/期望产出/优先级/附件 + 分派指针（007）。

    分派三列是不变量的一部分（state_machine.assert_dispatch_invariants）：
    ``assignee_id`` 非空 ⇔ 状态 ∈ {pending, accepted}；``accepted_at`` 非空
    ⇔ 状态 = accepted。``assignee_id`` 用 ON DELETE SET NULL —— 用户被删时
    需求本体不随之消失（「需求是已经发生过的事」，同 source_schedule_id 纪律），
    经手历史在 request_dispatches 表追溯，本表只存当前责任指针。
    """

    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    # 007：当前接收人（分派给员工的责任指针）。NULL = 无人认领。
    # 不带 index=True：复合索引 ix_requests_assignee_status 已覆盖其前缀，
    # 单列索引是纯冗余（同 Notification 的处理，索引只在 __table_args__ 声明）。
    assignee_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
            "pending",
            "accepted",
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
    # 007：最近一次分派/转交时间与当前接收人接收时间。退回时随 assignee 一并清空。
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attachments: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 来源调度（004）：可空。手工创建的需求为 NULL。
    # 用 ON DELETE SET NULL 而不是 CASCADE —— 删除调度规则绝不能连带删除它
    # 已经产生的需求（进而连带 Run、Step、制品）。规则是"以后怎么触发"，
    # 需求是"已经发生过的事情"，后者不因前者被撤销而消失。
    source_schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 008：业务归属（项目容器）。可空 = 未归类。ON DELETE SET NULL：
    # 项目删了，归类过的事实记录还在（research R-02，同 source_schedule_id 纪律）。
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    # 008：项目名展示快照。物化而不 JOIN（research R-04，同 source_schedule_name
    # 模式）：列表是「过滤 + count + 分页」三连，JOIN 会让三处都带上 projects。
    # 写入点只有两处（service）：建/改需求、项目改名；删项目时 service 显式清空
    # （外键 SET NULL 只动 project_id，不清它——不清会留下孤儿名字）。
    project_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner = relationship("User", back_populates="requests", foreign_keys=[owner_id])
    assignee = relationship("User", foreign_keys=[assignee_id])
    runs = relationship("Run", back_populates="request")
    source_schedule = relationship("Schedule", foreign_keys=[source_schedule_id])
    dispatches = relationship(
        "RequestDispatch",
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    notifications = relationship(
        "Notification",
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # 009：依赖边。dependency_edges = 我依赖谁（我是被依赖方，request_id 侧）；
    # dependent_edges = 谁依赖我（我是前置，dependency_id 侧）。两个 FK 指向
    # 同一张表，必须显式 foreign_keys——否则 SQLAlchemy 无法判断映射方向。
    # passive_deletes=True：删需求时边由 DB 级联删，ORM 不逐条 SELECT
    # （同 dispatches/notifications 纪律）。
    # ⚠️ 关系名刻意不叫 dependencies/dependents：与 RequestOut 的
    # `dependencies` 派生字段同名会让 pydantic from_attributes 读到关系、
    # 触发惰性载入（MissingGreenlet，009 Phase 3b 实测）。
    dependency_edges = relationship(
        "RequestDependency",
        foreign_keys=[RequestDependency.request_id],
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    dependent_edges = relationship(
        "RequestDependency",
        foreign_keys=[RequestDependency.dependency_id],
        back_populates="dependency",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # 收件箱热点查询（分派给我且 pending/accepted）的覆盖索引。
    # 与迁移 0011 声明一致——测试库走 create_all，索引必须声明在模型侧，
    # 否则「迁移里有、测试里没有」会让 prod/test 静默分叉。
    # 008：ix_requests_project_id 服务 GET /requests?project_id= 过滤。
    __table_args__ = (
        Index("ix_requests_assignee_status", "assignee_id", "status"),
        Index("ix_requests_project_id", "project_id"),
    )
