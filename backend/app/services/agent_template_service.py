"""Agent 模板服务（005）：CRUD + 另存为模板 + 快照隔离。

三条纪律（对应 spec 的 FR，写在这里免得每次改代码重新推导）：

1. **快照，不是引用**：`save_as_template` 把 Agent 的 10 个配置字段拷贝进模板行，
   之后两边互不影响（FR-002）。任何「让模板改动传导到 Agent」的设计都是回归。
2. **404 同措辞**：访问他人模板与访问不存在的模板返回同一条 message
   （FR-004）。用 403 区分会泄露资源存在性——与 004 FR-006 同一纪律。
3. **内置只读**：`owner_id IS NULL` 的行 update/delete 一律 FORBIDDEN（FR-015）。

事务边界遵循 003 审计改造后的约定：service 只 `flush`，`commit` 在端点层。
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.agent import Agent
from app.models.agent_template import AgentTemplate
from app.schemas.agent_template import AgentTemplateCreate, AgentTemplateUpdate

# 从 Agent 快照进模板的字段。顺序即文档顺序，加减字段必须同步 data-model.md。
SNAPSHOT_FIELDS = (
    "description",
    "system_prompt",
    "model",
    "temperature",
    "max_steps",
    "max_run_seconds",
    "tools",
    "cost_limit_cents",
    "egress_mode",
)


def _is_builtin(template: AgentTemplate) -> bool:
    return template.owner_id is None


async def create_template(
    db: AsyncSession, *, user_id: str, payload: AgentTemplateCreate
) -> AgentTemplate:
    """创建自己的模板（重名 → 409）。"""
    existing = await _find_by_name(db, owner_id=user_id, name=payload.name)
    if existing is not None:
        raise AppError("CONFLICT", "同名模板已存在")
    template = AgentTemplate(
        owner_id=user_id,
        name=payload.name,
        description=payload.description,
        system_prompt=payload.system_prompt,
        model=payload.model,
        temperature=payload.temperature,
        max_steps=payload.max_steps,
        max_run_seconds=payload.max_run_seconds,
        tools=list(payload.tools),
        cost_limit_cents=payload.cost_limit_cents,
        egress_mode=payload.egress_mode,
    )
    db.add(template)
    await db.flush()
    return template


async def _find_by_name(
    db: AsyncSession, *, owner_id: str | None, name: str
) -> AgentTemplate | None:
    stmt = select(AgentTemplate).where(
        AgentTemplate.owner_id == owner_id, AgentTemplate.name == name
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_template(
    db: AsyncSession, template_id: str, *, user_id: str
) -> AgentTemplate:
    """取单个模板：自己的或内置；其他一律 NOT_FOUND 同措辞。"""
    template = await db.get(AgentTemplate, template_id)
    if template is None:
        raise AppError("NOT_FOUND", "模板不存在")
    if template.owner_id != user_id and not _is_builtin(template):
        # 不区分「不存在」与「不是你的」——措辞必须一致（FR-004）。
        raise AppError("NOT_FOUND", "模板不存在")
    return template


async def list_templates(
    db: AsyncSession,
    *,
    user_id: str,
    scope: str = "all",
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AgentTemplate], int]:
    """模板列表：内置（全部）+ 自己的。

    `total` 是过滤后的全量条数，不是当页长度——前端靠它算总页数
    （与 `list_agents` 同一约定）。
    """
    if scope == "mine":
        conditions = [AgentTemplate.owner_id == user_id]
    elif scope == "builtin":
        conditions = [AgentTemplate.owner_id.is_(None)]
    else:
        conditions = [
            or_(AgentTemplate.owner_id == user_id, AgentTemplate.owner_id.is_(None))
        ]
    if q:
        like = f"%{q}%"
        conditions.append(
            or_(AgentTemplate.name.ilike(like), AgentTemplate.description.ilike(like))
        )

    total = (
        await db.execute(
            select(func.count()).select_from(AgentTemplate).where(*conditions)
        )
    ).scalar_one()

    stmt = (
        select(AgentTemplate)
        .where(*conditions)
        # 内置排在前：它们是「开箱可用」的那批，成员自己的按时间倒序跟在后面
        .order_by(
            AgentTemplate.owner_id.is_(None).desc(), AgentTemplate.created_at.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def update_template(
    db: AsyncSession,
    template_id: str,
    *,
    user_id: str,
    payload: AgentTemplateUpdate,
) -> AgentTemplate:
    """编辑自己的模板（内置 403；重名 409）。"""
    template = await get_template(db, template_id, user_id=user_id)
    if _is_builtin(template):
        raise AppError("FORBIDDEN", "内置模板不可编辑")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] != template.name:
        clash = await _find_by_name(db, owner_id=user_id, name=changes["name"])
        if clash is not None:
            raise AppError("CONFLICT", "同名模板已存在")
    for field, value in changes.items():
        setattr(template, field, value)
    await db.flush()
    return template


async def delete_template(db: AsyncSession, template_id: str, *, user_id: str) -> None:
    """删除自己的模板（内置 403）。

    已由它创建的 Agent 不受影响：外键 `ON DELETE SET NULL` 会把
    `agents.source_template_id` 置空（FR-021）。
    """
    template = await get_template(db, template_id, user_id=user_id)
    if _is_builtin(template):
        raise AppError("FORBIDDEN", "内置模板不可删除")
    await db.delete(template)
    await db.flush()


async def save_as_template(
    db: AsyncSession, *, agent: Agent, name: str, description: str | None = None
) -> AgentTemplate:
    """把一个 Agent 的当前配置快照成模板（US3）。

    归档态的 Agent 也允许——归档只影响可分配性，不影响配置的复用价值（US3 AS5）。
    重名不自动加后缀：模板库不该出现一堆「XX 副本」噪音（spec Assumptions）。
    """
    existing = await _find_by_name(db, owner_id=agent.owner_id, name=name)
    if existing is not None:
        raise AppError("CONFLICT", "同名模板已存在")
    snapshot = {field: getattr(agent, field) for field in SNAPSHOT_FIELDS}
    # tools 是可变 list，拷贝一份，避免模板与 Agent 共享同一个列表对象
    if snapshot.get("tools") is not None:
        snapshot["tools"] = list(snapshot["tools"])
    # description 来自参数而非快照：模板的「适用场景」与 Agent 的「一句话说明」
    # 是两件事，用户另存时通常要重写。
    snapshot.pop("description", None)
    template = AgentTemplate(
        owner_id=agent.owner_id,
        name=name,
        description=description,
        **snapshot,
    )
    db.add(template)
    await db.flush()
    return template
