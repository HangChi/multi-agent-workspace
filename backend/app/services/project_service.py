"""项目服务（008）：CRUD + 快照维护 + 删除的 SET NULL 语义。

与 request_service / assignment_service 的分工：本服务只管项目实体与
「项目 ↔ 需求归属」这一条边，不碰分派链路与执行。

三条写路径的快照纪律（data-model §2 注记）：
- 建/改需求时写 project_id + project_name（request_service 调用本服务的校验）；
- 项目改名时同事务 UPDATE requests.project_name；
- 删项目时外键只清 project_id，project_name 由本服务显式清——
  不清会留下「孤儿名字」，看板上显示一个已不存在的项目名。
"""

import structlog
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.project import Project
from app.models.request import Request
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectUpdate

logger = structlog.get_logger(__name__)


async def _load_owned_project(
    db: AsyncSession, project_id: str, user_id: str
) -> Project:
    """行级锁加载：不存在或非本人 → 404（不暴露存在性）。"""
    stmt = select(Project).where(Project.id == project_id).with_for_update()
    project = (await db.execute(stmt)).scalars().first()
    if project is None or project.owner_id != user_id:
        raise AppError("NOT_FOUND", "项目不存在")
    return project


async def _assert_name_available(
    db: AsyncSession, owner_id: str, name: str, *, exclude_id: str | None = None
) -> None:
    """同用户名下（含已归档项目）名字唯一，否则 409。

    先查后建给友好错误；并发同名由 uq_projects_owner_name 约束兜底
    （service 层不捕获 IntegrityError——端点统一错误处理会转 500，
    真并发撞约束的概率极低，宁可 500 暴露也不要静默吞掉）。
    """
    stmt = (
        select(func.count())
        .select_from(Project)
        .where(Project.owner_id == owner_id, Project.name == name)
    )
    if exclude_id:
        stmt = stmt.where(Project.id != exclude_id)
    if int((await db.execute(stmt)).scalar_one()) > 0:
        raise AppError("CONFLICT", "同名项目已存在（含已归档项目）")


async def _visible_request_counts(
    db: AsyncSession, owner_ids: list[str], project_ids: list[str]
) -> dict[str, int]:
    """按当前用户可见范围（我创建的 + 分派给我的）聚合各项目需求数。

    一次 GROUP BY，与列表同一次往返——不物化计数表（FR-010）。
    """
    if not project_ids:
        return {}
    stmt = (
        select(Request.project_id, func.count())
        .where(
            or_(
                Request.owner_id.in_(owner_ids),
                Request.assignee_id.in_(owner_ids),
            ),
            Request.project_id.in_(project_ids),
        )
        .group_by(Request.project_id)
    )
    # where 已过滤 project_id IN (...)（非空集合），mypy 推断不出列的可空性，
    # 显式 filter 既是类型收窄也是自文档。
    return {
        project_id: int(count)
        for project_id, count in (await db.execute(stmt)).all()
        if project_id
    }


async def list_projects(
    db: AsyncSession, user_id: str, scope: str = "mine"
) -> list[tuple[Project, int]]:
    """项目列表 + 每个项目的可见需求数。

    scope=mine（管理页默认）：仅我创建的项目。
    scope=relevant（看板筛选下拉）：我创建的 ∪ **派给我需求所属的项目**——
    接收人的下拉里必须能看到「派给你那件活属于哪个项目」，否则他的看板
    按项目筛选时该项目不可见（007 scope=relevant 的同款理由）。
    """
    if scope == "relevant":
        # 可见需求所属的项目 id（我创建的 + 分派给我的）
        visible_project_ids = list(
            (
                await db.execute(
                    select(Request.project_id).where(
                        or_(
                            Request.owner_id == user_id,
                            Request.assignee_id == user_id,
                        ),
                        Request.project_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        owned = list(
            (await db.execute(select(Project).where(Project.owner_id == user_id)))
            .scalars()
            .all()
        )
        referenced = list(
            (
                await db.execute(
                    select(Project).where(Project.id.in_(visible_project_ids))
                )
            )
            .scalars()
            .all()
        )
        merged = {p.id: p for p in owned}
        merged.update({p.id: p for p in referenced})
        ordered = sorted(merged.values(), key=lambda p: p.created_at, reverse=True)
    else:
        stmt = (
            select(Project)
            .where(Project.owner_id == user_id)
            .order_by(Project.created_at.desc())
        )
        ordered = list((await db.execute(stmt)).scalars().all())

    counts = await _visible_request_counts(db, [user_id], [p.id for p in ordered])
    return [(p, counts.get(p.id, 0)) for p in ordered]


async def get_project(
    db: AsyncSession, project_id: str, user_id: str
) -> tuple[Project, int]:
    """单个项目 + 可见需求数。"""
    project = await _load_owned_project(db, project_id, user_id)
    counts = await _visible_request_counts(db, [user_id], [project.id])
    return project, counts.get(project.id, 0)


async def create_project(
    db: AsyncSession, *, user_id: str, payload: ProjectCreate
) -> Project:
    """建项目（名字查重 + 409）。"""
    await _assert_name_available(db, user_id, payload.name)
    project = Project(
        owner_id=user_id,
        name=payload.name,
        description=payload.description,
    )
    db.add(project)
    await db.flush()
    logger.info("project_created", project_id=project.id, owner_id=user_id)
    return project


async def update_project(
    db: AsyncSession, *, project_id: str, user_id: str, payload: ProjectUpdate
) -> Project:
    """改项目：改名同事务同步 requests.project_name（快照双写点之一）。"""
    project = await _load_owned_project(db, project_id, user_id)
    data = payload.model_dump(exclude_unset=True)

    if data.get("name"):
        await _assert_name_available(db, user_id, data["name"], exclude_id=project.id)
        project.name = data["name"]
        # 快照同步：只此一处与 request_service 的建/改路径会写 project_name
        await db.execute(
            update(Request)
            .where(Request.project_id == project.id)
            .values(project_name=project.name)
        )
    if "description" in data:  # 可显式置空
        project.description = data["description"]
    if data.get("status"):
        if data["status"] not in ("active", "archived"):
            raise AppError("INVALID_INPUT", "status 仅支持 active/archived")
        project.status = data["status"]
    await db.flush()
    logger.info(
        "project_updated",
        project_id=project.id,
        fields=sorted(data.keys()),
        status=project.status,
    )
    return project


async def delete_project(db: AsyncSession, *, project_id: str, user_id: str) -> int:
    """删项目：外键把需求 project_id 置 NULL；本服务同事务清 project_name。

    Returns:
        int: 被解除归属的需求条数（审计 detail 用，contracts §1.4）。
    """
    project = await _load_owned_project(db, project_id, user_id)
    affected = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Request)
                .where(Request.project_id == project.id)
            )
        ).scalar_one()
    )
    # 先清快照再删项目：外键 SET NULL 在 DELETE 时生效，同事务内顺序无所谓，
    # 但先 UPDATE 能让 affected 与「name 被清空的行数」严格一致（可断言）。
    await db.execute(
        update(Request)
        .where(Request.project_id == project.id)
        .values(project_id=None, project_name=None)
    )
    await db.delete(project)
    await db.flush()
    logger.info("project_deleted", project_id=project_id, request_count=affected)
    return affected


async def load_active_owned_project(
    db: AsyncSession, project_id: str, user_id: str
) -> Project:
    """建/改需求时的项目校验（contracts §2.1）：存在 + 属本人 + active。

    不存在/属他人 → 404（归属纪律）；已归档 → 400（看得见但不能用，R-03）。
    """
    project = await db.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise AppError("NOT_FOUND", "项目不存在")
    if project.status != "active":
        raise AppError("INVALID_INPUT", "项目已归档，不能作为新需求归属")
    return project


async def project_visible_to_user(
    db: AsyncSession, project_id: str, user_id: str
) -> bool:
    """项目对当前用户是否可见：我创建的 ∪ **含我可见需求的项目**。

    与「管理页 mine 口径」的差异是刻意存在的：看板筛选下拉
    （scope=relevant）会把「派给我需求所属的他人项目」给接收人，
    他点这个项目时过滤校验必须放行——否则下拉给了个点了就 404 的选项
    （s9 场景 C 实测抓到的前后端不一致）。
    """
    project = await db.get(Project, project_id)
    if project is None:
        return False
    if project.owner_id == user_id:
        return True
    visible = (
        await db.execute(
            select(func.count())
            .select_from(Request)
            .where(
                Request.project_id == project_id,
                or_(
                    Request.owner_id == user_id,
                    Request.assignee_id == user_id,
                ),
            )
        )
    ).scalar_one()
    return int(visible) > 0


async def project_exists_for_filter(
    db: AsyncSession, project_id: str, user_id: str
) -> None:
    """`GET /requests?project_id=` 的过滤目标校验。

    允许已归档项目（归档只拦新建，不拦回看——Assumptions 第 2 条），
    但要求「存在且对当前用户可见」（本人或有我可见需求），否则 404——
    不是静默空列表，空列表会让人以为项目被清空了。
    """
    if not await project_visible_to_user(db, project_id, user_id):
        raise AppError("NOT_FOUND", "项目不存在")


async def user_exists(db: AsyncSession, user_id: str) -> bool:
    return await db.get(User, user_id) is not None
