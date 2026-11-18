"""项目成员模型（010）：共享关系。

一行 `(project_id, user_id)` = 该用户是该项目的成员。**无 role 列**——
成员身份即角色，owner 恒为 `projects.owner_id`（research R-01：两层模型
是用户确认范围，第三态没有使用数据；存一个只有单值的列是给未来埋坑）。

可增可删、不更新（改语义就是删旧建新，同 009 依赖边）。共享关系是
**派生数据**：删项目/删用户时级联消失，而需求不级联——需求是已发生的
事（008 source_schedule_id / 009 request_dependencies 的同款纪律）。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ProjectMember(Base):
    """一条项目成员关系。"""

    __tablename__ = "project_members"

    # 复合主键即唯一约束：成员关系无第二行，同名并发加入由 PK 兜底转 409
    # （不需要额外的 UniqueConstraint——PK 已经是）。
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # 「我是哪些项目的成员」反查——relevant 口径的热路径
        # （GET /projects?scope=relevant 与需求列表半连接都走它）。
        Index("ix_project_members_user_id", "user_id"),
    )
