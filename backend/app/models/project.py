"""项目模型（008）：需求的业务容器。

与 Agent/组的本质差异：Agent/组是**执行资源**（有策略、有成员、参与编排），
项目是**需求的业务归属**（没有成员、没有策略、不参与执行）。它回答的是
「这件活属于哪摊事」，不是「这件活谁来跑」。

单人归属（owner_id）是刻意选择：路线图把组织形态（共享项目/部门/矩阵）
后置到 P2，本轮用「每人管自己的项目」验证容器价值（research R-01）。
删除语义是 SET NULL——容器没了，归类过的事实记录还在（R-02，同 004
source_schedule_id 纪律）；真要「连需求一起消失」是删除需求自己的事。
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.user import new_uuid


class Project(Base):
    """一个项目：需求的容器。"""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    # 归属人。删用户 → 级联删项目 → 其下需求 project_id SET NULL（需求不删）。
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # ≤100 与 Agent/组名同纪律。唯一性按 owner 维度（R-07）：不同用户建同名
    # 项目太常见（「官网改版」这种名字），全局唯一会把第二个用户直接挡住。
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # active 可被新建需求选中；archived 只拦新建，既有归属与全部链路照常（R-03）。
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
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

    __table_args__ = (
        # 同用户名下项目名唯一（含已归档项目——归档仍占名字，防「归档后建同名」
        # 把历史归属搅浑，contracts §1.2）。并发同名第二人由约束兜底转 409。
        UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),
        # 列表端点固定按 owner 过滤 + 状态筛选。
        Index("ix_projects_owner_status", "owner_id", "status"),
    )
