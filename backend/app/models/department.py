"""部门（组织架构，020）：树形组织 + 分派范围策略。

为什么需要它：007 的分派是「任何在职员工都能被指派」——公司一大就变成
「隔壁部门的活也往我这塞」。020 给组织一个最小模型：部门树 + 每层一个
**分派范围**策略，让「谁能把活派给谁」可配置。

三个刻意的最小化：

1. **物化路径**（`path` = `/root/child/`）而不是递归 CTE：
   子树查询是 `path LIKE '/x/%'`，一次索引扫描；代价是移动部门要重写子树 path
   （部门移动是低频操作，值得换来读路径的简单）。
2. **策略只有三档**：`all`（全公司）/ `subtree`（本部门及下级）/ `self`（仅本部门）。
   不做逐人授权——权限矩阵的复杂度会指数上升，而真实诉求只有「别让隔壁部门乱派」。
3. **默认不收紧**：`assign_scope` 默认 `all`，且**没有部门的用户按 `all` 处理**。
   这样既有部署升级后行为零变化；管理员建部门、设策略后才逐步生效
   （升级不改变现有权限，是能被接受的唯一方式）。
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.user import new_uuid

# 分派范围：能把自己部门的需求派给谁
SCOPE_ALL = "all"
SCOPE_SUBTREE = "subtree"
SCOPE_SELF = "self"


class Department(Base):
    """一个部门节点。"""

    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 父部门。删父节点会级联删子树（前端二次确认时说明「子部门一并删除」）。
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), nullable=True
    )
    # 物化路径：`/根id/子id/`（含自身，以 / 结尾便于 LIKE 前缀匹配）
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    depth: Mapped[int] = mapped_column(nullable=False, default=0)
    # 该部门成员可分派的范围（020 的策略核心）
    assign_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SCOPE_ALL, server_default=SCOPE_ALL
    )
    # 022：每月可发放的邀请名额上限（max_uses 之和）。NULL = 不限。
    # 放在部门上而不是全局：招人预算是按部门的，全局配额只会让某个部门先到先得。
    invite_quota: Mapped[int | None] = mapped_column(nullable=True)
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
        # 同一父下不重名（跨父可以同名：两个部门都叫「平台组」是现实）
        UniqueConstraint("parent_id", "name", name="uq_departments_parent_name"),
        CheckConstraint(
            "assign_scope IN ('all', 'subtree', 'self')",
            name="ck_departments_assign_scope",
        ),
        # 子树查询（path LIKE '/x/%'）与树形渲染（按 path 排序）都走它
        Index("ix_departments_path", "path"),
    )

    # 刻意不声明 ORM 关系：
    # 1. 子树查询走物化路径（path LIKE），不需要 children 集合；
    # 2. 删父删子由 DB 的 parent_id ON DELETE CASCADE 完成（passive）；
    # 3. 自引用关系写错方向（remote_side 放错侧）会让**整个 mapper 配置失败**，
    #    代价是全部模型都起不来——收益为零时不该冒这个风险。
