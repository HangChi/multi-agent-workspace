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


# ===== 实例化：从模板创建 / 克隆（US1 / US2）=====


def instantiate(
    template, *, payload: dict | None, enabled_tools: list[str]
) -> tuple[Agent, list[str]]:
    """由模板求出一个待落库的 Agent 草稿（contracts §4.1）。

    语义：**模板值打底 → payload 中出现（且非 None）的字段覆盖**。
    `payload` 为 None 表示「一键确认，一个字都不改」。

    > payload 的 None 语义：**一律视为「未提供」**。前端表单永远提交完整对象，
    > 用户没碰的字段就是 null；若把 null 当「清空」，一个没碰描述的用户就会
    > 静默抹掉模板里写好的适用场景。要改描述就显式填值。

    工具过滤（FR-008）：**最终**工具集里不在 `enabled_tools` 中的项被剔除，
    剔除项原样返回给调用方拼 warning。**不静默丢弃**，也**不因此失败**——
    一次工具下线不该废掉一个模板，也不该让原样提交表单的用户撞 422。

    「最终工具集」= payload 的 tools（用户显式选过）否则模板的 tools。
    两边都可能带失效项：模板的可能过期，用户的可能来自一个旧的工具目录缓存。
    """
    values = {field: getattr(template, field) for field in SNAPSHOT_FIELDS}

    # null 一律当「没填」：见上方 docstring 的 None 语义说明。
    overrides = {k: v for k, v in (payload or {}).items() if v is not None}
    overrides.pop("tools", None)
    values.update(overrides)

    effective_tools = (payload or {}).get("tools")
    if effective_tools is None:
        effective_tools = values.get("tools") or []
    enabled = set(enabled_tools)
    kept = list(dict.fromkeys(t for t in effective_tools if t in enabled))
    filtered = [t for t in effective_tools if t not in enabled]
    values["tools"] = kept

    draft = Agent(**values)
    return draft, filtered


def clone_agent(source, *, owner_id: str, used_names: set[str]) -> Agent:
    """拷贝源 Agent 的配置，派生一个不冲突的名称（contracts §4.2）。

    名称规则：`{源名称} 副本`；已占用则 `副本2`、`副本3`…（上限 10 次）。
    基名超长时先截断，保证总长不破 `agents.name` 的 100 字符。

    只拷配置，**不拷实例级数据**（FR-011）：调用方拿到的是一个全新 Agent 行，
    没有 runs/steps/artifacts、没有组成员关系、没有外部工具授权与出站规则。

    不提交。
    """
    values = {field: getattr(source, field) for field in SNAPSHOT_FIELDS}
    tools = values.get("tools")
    if tools is not None:
        values["tools"] = list(tools)
    values["owner_id"] = owner_id
    values["status"] = "active"  # 归档源的克隆体仍是活跃态（FR-014）
    values["name"] = _derive_clone_name(source.name, used_names)
    return Agent(**values)


def _derive_clone_name(base_name: str, used_names: set[str]) -> str:
    """`{base} 副本` → `{base} 副本2` → …；10 次仍冲突则抛错。"""
    limit = 100
    for attempt in range(1, 11):
        suffix = " 副本" if attempt == 1 else f" 副本{attempt}"
        room = limit - len(suffix)
        candidate = base_name[:room] + suffix if room > 0 else suffix.strip()
        if candidate not in used_names:
            return candidate
    raise AppError(
        "CONFLICT", "克隆名称冲突次数过多（10 个副本名均已存在），请先修改源 Agent 名称"
    )
