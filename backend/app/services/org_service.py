"""组织架构服务（020）：部门树 + 分派范围。

核心只有一件事：**谁能把活派给谁**。

```
actor 的部门策略决定了可指派目标集合：
  all      → 全公司（默认；未归属部门的用户也按 all）
  subtree  → 本部门 + 所有下级部门
  self     → 仅本部门
```

设计取舍（research 里详述）：
- 物化路径查子树（`path LIKE '/x/%'`），不做递归 CTE；
- 默认不收紧：升级后行为零变化，管理员建树设策略才生效；
- 不支持移动部门（改 parent）——需要重建路径，而重组的真实频率极低，
  真要挪就「建新部门 + 把人挪过去」，比静默搬子树安全。
"""

from __future__ import annotations

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.department import SCOPE_ALL, SCOPE_SELF, SCOPE_SUBTREE, Department
from app.models.user import User

logger = structlog.get_logger(__name__)

_VALID_SCOPES = (SCOPE_ALL, SCOPE_SUBTREE, SCOPE_SELF)


async def list_tree(db: AsyncSession) -> list[tuple[Department, int]]:
    """全部部门（按 path 排序 = 树的先序），带回成员数。

    成员数一次 GROUP BY，不逐部门数（管理页要显示「这个部门几个人」）。
    """
    rows = list(
        (await db.execute(select(Department).order_by(Department.path))).scalars().all()
    )
    counts = dict(
        (
            await db.execute(
                select(User.department_id, func.count())
                .where(User.department_id.is_not(None))
                .group_by(User.department_id)
            )
        ).all()
    )
    return [(d, int(counts.get(d.id, 0))) for d in rows]


async def create_department(
    db: AsyncSession, *, name: str, parent_id: str | None, assign_scope: str = SCOPE_ALL
) -> Department:
    name = name.strip()
    if not name:
        raise AppError("INVALID_INPUT", "部门名不能为空白")
    if assign_scope not in _VALID_SCOPES:
        raise AppError("INVALID_INPUT", "分派范围仅支持 all / subtree / self")

    parent: Department | None = None
    if parent_id:
        parent = await db.get(Department, parent_id)
        if parent is None:
            raise AppError("NOT_FOUND", "上级部门不存在")

    # **先查重名，再插入**：`flush()` 阶段就会撞 uq_departments_parent_name，
    # 那时抛的是 IntegrityError（端点会转 500），用户看不到「重名」这句话。
    await _assert_unique_name(db, name=name, parent_id=parent_id)

    dept = Department(
        name=name,
        parent_id=parent_id,
        path="",  # 先占位，flush 拿到 id 后再拼（id 由 ORM 默认生成）
        depth=(parent.depth + 1) if parent else 0,
        assign_scope=assign_scope,
    )
    db.add(dept)
    await db.flush()
    dept.path = f"{parent.path if parent else '/'}{dept.id}/"
    await db.flush()

    logger.info("department_created", department_id=dept.id, parent_id=parent_id)
    return dept


async def _assert_unique_name(
    db: AsyncSession, *, name: str, parent_id: str | None, exclude_id: str | None = None
) -> None:
    """同一父下不重名（DB 有 UNIQUE，这里先查是为了给可读的 409）。

    必须在 INSERT 之前调用：`flush()` 就会校验 UNIQUE，届时抛 IntegrityError
    会被端点统一转成 500，用户只看到「服务器错误」。
    """
    stmt = select(func.count()).select_from(Department).where(Department.name == name)
    stmt = (
        stmt.where(Department.parent_id.is_(None))
        if parent_id is None
        else stmt.where(Department.parent_id == parent_id)
    )
    if exclude_id:
        stmt = stmt.where(Department.id != exclude_id)
    if int((await db.execute(stmt)).scalar_one()) > 0:
        raise AppError("CONFLICT", f"同一个上级下已有名为「{name}」的部门")


async def update_department(
    db: AsyncSession,
    *,
    department_id: str,
    name: str | None = None,
    assign_scope: str | None = None,
) -> Department:
    """改名 / 改分派范围。**不支持换上级**（见模块 docstring）。"""
    dept = await db.get(Department, department_id)
    if dept is None:
        raise AppError("NOT_FOUND", "部门不存在")

    if name is not None:
        stripped = name.strip()
        if not stripped:
            raise AppError("INVALID_INPUT", "部门名不能为空白")
        await _assert_unique_name(
            db, name=stripped, parent_id=dept.parent_id, exclude_id=dept.id
        )
        dept.name = stripped
        await db.flush()
    if assign_scope is not None:
        if assign_scope not in _VALID_SCOPES:
            raise AppError("INVALID_INPUT", "分派范围仅支持 all / subtree / self")
        dept.assign_scope = assign_scope
    await db.flush()
    return dept


async def delete_department(db: AsyncSession, *, department_id: str) -> None:
    """删部门：子树一并删除，成员归属清空（users.department_id SET NULL）。"""
    dept = await db.get(Department, department_id)
    if dept is None:
        raise AppError("NOT_FOUND", "部门不存在")
    subtree = await _subtree_ids(db, dept)
    # 先把人摘出来（SET NULL 靠 DB 也行，但显式做一次便于审计与测试断言）
    members = (
        await db.execute(select(User).where(User.department_id.in_(subtree)))
    ).scalars()
    for user in members:
        user.department_id = None
    await db.delete(dept)
    await db.flush()
    logger.info("department_deleted", department_id=department_id, subtree=len(subtree))


async def _subtree_ids(db: AsyncSession, dept: Department) -> list[str]:
    rows = (
        await db.execute(
            select(Department.id).where(Department.path.like(f"{dept.path}%"))
        )
    ).scalars()
    return list(rows.all())


async def set_user_department(
    db: AsyncSession, *, user_id: str, department_id: str | None
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "用户不存在")
    if department_id is not None:
        dept = await db.get(Department, department_id)
        if dept is None:
            raise AppError("NOT_FOUND", "部门不存在")
    user.department_id = department_id
    await db.flush()
    logger.info("user_department_set", user_id=user_id, department_id=department_id)
    return user


# ---------------------------------------------------------------- 分派范围


async def allowed_target_department_ids(
    db: AsyncSession, actor_id: str
) -> set[str] | None:
    """actor 能指派到的部门集合；`None` = 不限（全公司）。

    返回 None 的三种情况：actor 没有部门、部门策略是 all、部门已不存在。
    这不是「漏判」，是刻意的向后兼容：升级后谁都不会因为没配部门而派不出活。
    """
    actor = await db.get(User, actor_id)
    if actor is None or actor.department_id is None:
        return None
    dept = await db.get(Department, actor.department_id)
    if dept is None or dept.assign_scope == SCOPE_ALL:
        return None
    if dept.assign_scope == SCOPE_SELF:
        return {dept.id}
    return set(await _subtree_ids(db, dept))


async def can_assign_to(db: AsyncSession, actor_id: str, target_user_id: str) -> bool:
    """actor 能不能把需求指派给 target。

    四条放行：目标就是自己；actor 不受限；目标在允许部门内。
    「目标没有部门」在受限时**不放行**——未归属部门的人不在任何范围内，
    这是「先归档再限额」的必然结果（管理页有提示）。
    """
    if actor_id == target_user_id:
        return True
    allowed = await allowed_target_department_ids(db, actor_id)
    if allowed is None:
        return True
    target = await db.get(User, target_user_id)
    if target is None or target.department_id is None:
        return False
    return target.department_id in allowed


async def allowed_user_ids(db: AsyncSession, actor_id: str) -> list[str] | None:
    """可指派用户 id 列表（`None` = 不限），供 `/users/options` 过滤。

    这里把「自己」也带上：受限用户至少能把自己的活派给自己（或转交给
    自己的小号/自己负责的其他账号），否则连取消分派都没有落点。
    """
    allowed = await allowed_target_department_ids(db, actor_id)
    if allowed is None:
        return None
    rows = (
        await db.execute(
            select(User.id).where(
                User.department_id.in_(allowed), User.status == "active"
            )
        )
    ).scalars()
    return [*list(rows.all()), actor_id]
