"""Agent 模板服务（005）：CRUD + 另存为模板 + 快照隔离。

三条纪律（对应 spec 的 FR，写在这里免得每次改代码重新推导）：

1. **快照，不是引用**：`save_as_template` 把 Agent 的 10 个配置字段拷贝进模板行，
   之后两边互不影响（FR-002）。任何「让模板改动传导到 Agent」的设计都是回归。
2. **404 同措辞**：访问他人模板与访问不存在的模板返回同一条 message
   （FR-004）。用 403 区分会泄露资源存在性——与 004 FR-006 同一纪律。
3. **内置只读**：`owner_id IS NULL` 的行 update/delete 一律 FORBIDDEN（FR-015）。

事务边界遵循 003 审计改造后的约定：service 只 `flush`，`commit` 在端点层。
"""

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.agent import Agent
from app.models.agent_template import (
    KEEP_VERSIONS,
    AgentTemplate,
    AgentTemplateVersion,
)
from app.schemas.agent_template import (
    AgentTemplateCreate,
    AgentTemplateOut,
    AgentTemplateUpdate,
)

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


def to_out(template: AgentTemplate) -> AgentTemplateOut:
    """ORM 行 → 响应模型，派生字段统一在这里算（R5）。

    在此之前有两处各写一份：
    - `api/v1/agent_templates.py` 的 `_out`：`is_builtin = owner_id is None`，
      且内置模板把 visibility 强制成 team；
    - `api/v1/agents.py` 的「另存为模板」：硬写 `out.is_builtin = False`。

    后者当时是对的（另存出来的模板必然有 owner），但那是**推理**而不是**声明**：
    哪天 `save_as_template` 允许 owner 为空（系统预置 / 迁移导入），那行会静默
    把一个内置模板报成非内置，而「内置 = 只读」正是 005 FR-015 的判断依据。
    """
    out = AgentTemplateOut.model_validate(template)
    out.is_builtin = _is_builtin(template)
    # 023：内置模板对所有成员可用，语义上就是 team（不落库，派生）
    if out.is_builtin:
        out.visibility = "team"
    return out


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
    """取单个模板：自己的 / 内置 / 别人设为 team 的；其余一律 NOT_FOUND 同措辞。

    023 起多一支「团队共享」：可见（可读、可从模板创建）不等于可改——
    写操作在 `update_template` / `restore_version` 里单独判 owner。
    """
    template = await db.get(AgentTemplate, template_id)
    if template is None:
        raise AppError("NOT_FOUND", "模板不存在")
    visible = (
        template.owner_id == user_id
        or _is_builtin(template)
        or template.visibility == "team"
    )
    if not visible:
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
    elif scope == "team":
        # 023：别人共享给团队的模板（不含自己的、不含内置——内置由 builtin 单列）
        conditions = [
            AgentTemplate.visibility == "team",
            AgentTemplate.owner_id.is_not(None),
            AgentTemplate.owner_id != user_id,
        ]
    else:
        # all = 内置 + 自己的 + 团队共享的（只扩大不缩小，同 007 的 relevant 纪律）
        conditions = [
            or_(
                AgentTemplate.owner_id == user_id,
                AgentTemplate.owner_id.is_(None),
                AgentTemplate.visibility == "team",
            )
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
    # 023：团队共享让「可见」与「可改」分家了——必须显式判所有者，
    # 否则队友能改你的模板（get_template 已经放行了可见性）。
    if template.owner_id != user_id:
        raise AppError("FORBIDDEN", "只能编辑自己的模板")
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] != template.name:
        clash = await _find_by_name(db, owner_id=user_id, name=changes["name"])
        if clash is not None:
            raise AppError("CONFLICT", "同名模板已存在")
    if not changes:
        return template
    # 023：先把**修改前**的状态存成版本，再落新值。
    # 顺序不能反——反了就变成「存的是改后状态」，回滚永远回到同一份。
    await _record_version(db, template, actor_id=user_id)
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


# ---------------------------------------------------------------- 版本历史（023）

# 版本快照 = 既有 SNAPSHOT_FIELDS（9 个配置字段，005 定义）+ 名字。
# 复用而不是再写一份：两份字段列表迟早分叉，而分叉的症状是「回滚少还原一个字段」。
# 不含 visibility / owner：那是共享关系，不是配置（回滚配置不该改共享状态）。
VERSION_SNAPSHOT_FIELDS = ("name", *SNAPSHOT_FIELDS)


def snapshot_of(template: AgentTemplate) -> dict:
    return {field: getattr(template, field) for field in VERSION_SNAPSHOT_FIELDS}


async def _record_version(
    db: AsyncSession, template: AgentTemplate, *, actor_id: str | None
) -> AgentTemplateVersion:
    """把模板当前状态存成一个新版本，并裁掉过旧的（保留 KEEP_VERSIONS 个）。"""
    current_max = int(
        (
            await db.execute(
                select(func.coalesce(func.max(AgentTemplateVersion.version), 0)).where(
                    AgentTemplateVersion.template_id == template.id
                )
            )
        ).scalar_one()
    )
    version = AgentTemplateVersion(
        template_id=template.id,
        version=current_max + 1,
        snapshot=snapshot_of(template),
        created_by=actor_id,
    )
    db.add(version)
    await db.flush()

    # 裁剪：只留最近 KEEP_VERSIONS 个（版本号大的新）
    stale = (
        (
            await db.execute(
                select(AgentTemplateVersion.id).where(
                    AgentTemplateVersion.template_id == template.id,
                    AgentTemplateVersion.version <= current_max + 1 - KEEP_VERSIONS,
                )
            )
        )
        .scalars()
        .all()
    )
    if stale:
        await db.execute(
            delete(AgentTemplateVersion).where(AgentTemplateVersion.id.in_(stale))
        )
        await db.flush()
    return version


async def list_versions(
    db: AsyncSession, template_id: str, *, user_id: str
) -> list[AgentTemplateVersion]:
    """版本列表（新的在前）。读权限 = 能看到这个模板（自己的 / 团队 / 内置）。"""
    await get_template(db, template_id, user_id=user_id)
    rows = (
        await db.execute(
            select(AgentTemplateVersion)
            .where(AgentTemplateVersion.template_id == template_id)
            .order_by(AgentTemplateVersion.version.desc())
        )
    ).scalars()
    return list(rows.all())


async def restore_version(
    db: AsyncSession, template_id: str, version: int, *, user_id: str
) -> AgentTemplate:
    """把某个快照写回模板（只写配置与名字/描述，不动 visibility）。

    回滚本身也是一次修改：先把当前状态存成新版本，否则「回滚错了再回滚回去」
    就没有目标版本可用。
    """
    template = await get_template(db, template_id, user_id=user_id)
    if _is_builtin(template):
        raise AppError("FORBIDDEN", "内置模板不可编辑")
    if template.owner_id != user_id:
        raise AppError("FORBIDDEN", "只能回滚自己的模板")

    row = (
        await db.execute(
            select(AgentTemplateVersion).where(
                AgentTemplateVersion.template_id == template_id,
                AgentTemplateVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError("NOT_FOUND", "该版本不存在")

    snapshot = dict(row.snapshot)
    # 名字可能已被别的模板占用：回滚不还原名字，避免 409 把「回滚」变成死路
    snapshot.pop("name", None)
    await _record_version(db, template, actor_id=user_id)
    for field, value in snapshot.items():
        if field in VERSION_SNAPSHOT_FIELDS and field != "name":
            setattr(template, field, value)
    await db.flush()
    return template


# ---------------------------------------------------------------- 导入导出（023）

EXPORT_SCHEMA_VERSION = 1


def export_payload(template: AgentTemplate) -> dict:
    """导出为可移植 JSON。`schema_version` 让未来的格式变更能被识别。"""
    payload = {"schema_version": EXPORT_SCHEMA_VERSION}
    payload.update(snapshot_of(template))
    return payload


async def import_template(
    db: AsyncSession, *, user_id: str, payload: dict
) -> AgentTemplate:
    """从导出的 JSON 创建一份**新**模板（不改动任何既有模板）。

    名字冲突直接 409：自动加后缀（`xxx-2`）会让人在列表里看到一堆看不出
    区别的模板，而「你导过一份了」是个应当被告诉的事实。
    """
    version = payload.get("schema_version")
    if version != EXPORT_SCHEMA_VERSION:
        raise AppError(
            "INVALID_INPUT",
            f"不支持的模板格式版本：{version}（当前支持 {EXPORT_SCHEMA_VERSION}）",
        )

    data = {field: payload.get(field) for field in VERSION_SNAPSHOT_FIELDS}
    if not data.get("name"):
        raise AppError("INVALID_INPUT", "模板内容缺少 name")
    from app.schemas.agent_template import AgentTemplateCreate

    # 用创建 schema 复校验一遍：字段范围、工具白名单都在这里，
    # 导入不构成绕过校验的旁路（同 005 FR-022 的纪律）
    try:
        validated = AgentTemplateCreate(
            **{k: v for k, v in data.items() if v is not None}
        )
    except Exception as exc:
        raise AppError("INVALID_INPUT", f"模板内容不合法：{str(exc)[:300]}") from exc

    return await create_template(db, user_id=user_id, payload=validated)


# ---------------------------------------------------------------- 模板市场（026）


async def _unique_copy_name(db: AsyncSession, owner_id: str, base: str) -> str:
    """给自己复制一份时的名字：原名 + 「副本」；再冲突就加序号。

    这里**自动加后缀**是合理的（与 023 导入的 409 相反）：复制是显式的「我要
    再来一份」，系统拒绝它没有任何好处；而导入是「这份我是不是已经有了」的
    疑问，需要用户自己判断。
    """
    candidate = f"{base}（副本）"
    if await _find_by_name(db, owner_id=owner_id, name=candidate) is None:
        return candidate
    for index in range(2, 100):
        numbered = f"{base}（副本 {index}）"
        if await _find_by_name(db, owner_id=owner_id, name=numbered) is None:
            return numbered
    raise AppError("CONFLICT", "同名副本过多，请先清理")


async def duplicate_template(
    db: AsyncSession, template_id: str, *, user_id: str
) -> AgentTemplate:
    """把一个可见的模板复制成自己的私有副本（026「模板市场」的核心动作）。

    能复制的前提是「能看到」（自己的 / 内置 / 别人的 team），这一点由
    `get_template` 保证；复制结果恒为 private——从别人那里拿来的东西，
    不该默认再共享出去。
    """
    source = await get_template(db, template_id, user_id=user_id)
    if source.owner_id == user_id:
        raise AppError("CONFLICT", "这已经是你的模板，不需要复制")

    from app.schemas.agent_template import AgentTemplateCreate

    payload = AgentTemplateCreate(
        name=await _unique_copy_name(db, user_id, source.name),
        description=source.description,
        system_prompt=source.system_prompt,
        model=source.model,
        temperature=source.temperature,
        max_steps=source.max_steps,
        max_run_seconds=source.max_run_seconds,
        tools=list(source.tools or []),
        cost_limit_cents=source.cost_limit_cents,
        egress_mode=source.egress_mode,
    )
    return await create_template(db, user_id=user_id, payload=payload)
