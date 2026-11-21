"""项目成员模型（010，019 加 role）。

一行 `(project_id, user_id)` = 该用户是该项目的成员。owner 恒为
`projects.owner_id`，**不在本表**（R-01：双写要同步是纯冗余）。

019 加 `role`：`editor`（默认，读写）/ `viewer`（只读）。
010 当时明确「不设 role」的理由是「成员身份即角色、第三态没有使用数据」——
那个判断在「所有成员权限完全一样」时成立；一旦出现「只要看、不要改」的诉求
（外部协作方、审计、跨团队知情），二元模型只能靠「不加成员」来近似，
连可见性一起丢掉。所以 019 补这一列，但**只补两个值**，不做权限矩阵。

可增可删、不修改语义（改角色是唯一的更新动作）；共享关系是**派生数据**：
删项目/删用户时级联消失，而需求不级联（需求是已发生的事）。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"


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
    # 019：项目内角色。默认 editor = 010 的既有行为，迁移零回填。
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ROLE_EDITOR, server_default=ROLE_EDITOR
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("role IN ('editor', 'viewer')", name="ck_project_members_role"),
        # 「我是哪些项目的成员」反查——relevant 口径的热路径
        # （GET /projects?scope=relevant 与需求列表半连接都走它）。
        Index("ix_project_members_user_id", "user_id"),
    )
