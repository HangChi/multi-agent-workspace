"""依赖服务（009）：边 CRUD + 成环检测 + 门禁查询 + 解锁反查。

四条路径的分工：
- 边 CRUD（add/remove/list）：服务详情弹层；
- `would_create_cycle`：纯函数 BFS，加边前的成环判定（可脱离 DB 单测）；
- `assert_not_blocked`：assign 热路径的门禁（一次半连接，SC-003）；
- `ready_dependents`：settle 终态后的解锁反查（「谁在等我刚收敛的这条」）。

不碰状态机（R-03：门禁是校验不是状态）、不碰配额口径、不做拓扑排序
（用户范围外）。
"""

from collections import deque

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.request import Request
from app.models.request_dependency import RequestDependency
from app.models.user import User
from app.orchestrator.request_state_machine import ACTIVE_STATUSES, TERMINAL_STATUSES
from app.services import project_member_service, request_service

logger = structlog.get_logger(__name__)

# 终态集合（**需求**状态，FR-006/R-01）：门禁看「是否终态」而非「是否成功」——
# failed/cancelled 的前置同样解锁，成败判断交给人（通知说明实际终态）。
#
# R3：`TERMINAL_STATUSES` 现在是上面 `request_state_machine` 的引用，本模块
# 不再各写一份。原注释已指出「不含 timeout（那是 Run 状态，枚举里没有，
# NOT IN 绑定会报错）」，但同文件 `ready_dependents` 的 skip 集合里偏偏混进了
# `"timeout"`——同一个文件内自相矛盾，正是字面量散落的典型后果。
#
# 用 `from ... import` 引入后，模块属性 `dependency_service.TERMINAL_STATUSES`
# 仍可访问（api/v1/requests.py 依赖它），无需额外赋值。

#: `_adjacency_for` 的 BFS 深度上限（安全阀，见函数注释）。
#: 依赖链正常只有几层；上限只是防止异常数据让一次加边请求吃掉整库。
_MAX_ADJACENCY_DEPTH = 16


class DependencyBlockedError(AppError):
    """门禁异常（009）：assign 被未完成前置挡下。

    为什么是子类而不是新错误码：HTTP 层仍按父类 code=CONFLICT 映射 409
    （契约不变），但调度分支要区分「正常门禁」与「真失败」——前者建单
    不分配（FR-009），后者走回滚记 failed。用字符串匹配 message 太脆，
    用类型匹配。
    """

    def __init__(self, message: str) -> None:
        super().__init__("CONFLICT", message)


def would_create_cycle(
    adjacency: dict[str, set[str]], request_id: str, dependency_id: str
) -> bool:
    """加边 (request_id 依赖 dependency_id) 是否会成环。

    判据：从 dependency_id 沿既有边（被依赖方 → 前置）BFS，若能到达
    request_id，则新边会闭合一个环。自依赖直接判真（service 层先拦 400，
    这里是兜底）。

    BFS 而非递归 CTE（R-04）：单需求的依赖子图是小图，实测数据量下
    内存遍历足够；CTE 的复杂度没有对应收益，且无法纯函数单测。
    """
    if request_id == dependency_id:
        return True
    seen: set[str] = set()
    queue: deque[str] = deque([dependency_id])
    while queue:
        node = queue.popleft()
        if node == request_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        queue.extend(adjacency.get(node, ()))
    return False


async def _adjacency_for(db: AsyncSession, request_id: str) -> dict[str, set[str]]:
    """取某个需求所在连通域的邻接表（被依赖方 → 前置集合）。

    R3 观察项：原实现 `SELECT request_id, dependency_id` 无条件全表扫，
    每次加边都拉一遍**所有**依赖边。注释当时写的是「数据量小可接受」，
    但这是唯一一条会随租户数据总量线性劣化的路径——别人的依赖图也会
    被读出来，加上边是交互式操作，代价会随系统变老单调上升。

    改为按连通域收窄（双向 BFS，一条 SQL 取完整层）：
      - 下游层：谁依赖我
      - 上游层：我依赖谁
    成环判定只需要「从 dependency_id 出发能否到达 request_id」这一条路径
    所在的连通域，域外节点永远不影响结果。

    深度上限 `_MAX_ADJACENCY_DEPTH` 是安全阀：依赖链正常情况下只有几层，
    真出现超深链（或数据异常）时宁可退化成「按已读到的部分判定」，
    也不让一次加边请求吃掉整库。
    """
    graph: dict[str, set[str]] = {}
    seen: set[str] = {request_id}
    frontier: list[str] = [request_id]
    for _ in range(_MAX_ADJACENCY_DEPTH):
        if not frontier:
            break
        # 双向一次取全：给定一批 id，取出「谁依赖它们」（下游）与
        # 「它们依赖谁」（上游）两组边。这两组边一起构成连通域的自环邻域。
        rows = (
            await db.execute(
                select(
                    RequestDependency.request_id, RequestDependency.dependency_id
                ).where(
                    RequestDependency.request_id.in_(frontier)
                    | RequestDependency.dependency_id.in_(frontier)
                )
            )
        ).all()
        next_frontier: list[str] = []
        for rid, did in rows:
            graph.setdefault(rid, set()).add(did)
            for node in (rid, did):
                if node not in seen:
                    seen.add(node)
                    next_frontier.append(node)
        frontier = next_frontier
    return graph


async def _load_editable_request(
    db: AsyncSession, request_id: str, user_id: str
) -> Request:
    """被依赖方（request_id 侧）的编辑权校验：owner-or-assignee 或项目成员，
    否则 404。

    R1：谓词走 `request_service.is_participant`（单一判定点），这里只补
    「项目成员」这一肢并翻译成 404。原注释说「刻意不借 get_request，因为
    它对非属主抛 403」——那条理由在 016 之后已不成立（`_check_owner` 早已
    改成 404），但本函数保留独立实现仍有价值：它要的是**可编辑**语义，
    而 `get_request` 的 `allow_member` 默认关闭，直接调用会漏掉成员分支。

    010：谓词加一肢「项目成员」——成员管理共享项目里的依赖边。
    """
    request = await db.get(Request, request_id)
    if request is None:
        raise AppError("NOT_FOUND", "需求不存在")
    if request_service.is_participant(request, user_id):
        return request
    if request.project_id and await project_member_service.is_participant(
        db, request.project_id, user_id
    ):
        return request
    raise AppError("NOT_FOUND", "需求不存在")


async def add_dependency(
    db: AsyncSession, *, request_id: str, dependency_id: str, user_id: str
) -> RequestDependency:
    """加边。判定顺序同 contracts §1.2：自依赖 400 → 目标 404 → 重复 409
    → 成环 409。"""
    if request_id == dependency_id:
        raise AppError("INVALID_INPUT", "需求不能依赖自己")
    await _load_editable_request(db, request_id, user_id)
    target = await db.get(Request, dependency_id)
    if target is None:
        raise AppError("NOT_FOUND", "前置需求不存在")

    existing = (
        (
            await db.execute(
                select(RequestDependency).where(
                    RequestDependency.request_id == request_id,
                    RequestDependency.dependency_id == dependency_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise AppError("CONFLICT", "该依赖已存在")

    adjacency = await _adjacency_for(db, request_id)
    if would_create_cycle(adjacency, request_id, dependency_id):
        raise AppError("CONFLICT", "会形成循环依赖，请调整边的方向")

    edge = RequestDependency(
        request_id=request_id,
        dependency_id=dependency_id,
        created_by=user_id,
    )
    db.add(edge)
    await db.flush()
    logger.info(
        "dependency_added",
        request_id=request_id,
        dependency_id=dependency_id,
        by=user_id,
    )
    return edge


async def remove_dependency(
    db: AsyncSession, *, request_id: str, dependency_id: str, user_id: str
) -> None:
    """删边。被依赖方编辑权校验同加边；边不存在 → 404。"""
    await _load_editable_request(db, request_id, user_id)
    edge = (
        (
            await db.execute(
                select(RequestDependency).where(
                    RequestDependency.request_id == request_id,
                    RequestDependency.dependency_id == dependency_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if edge is None:
        raise AppError("NOT_FOUND", "依赖关系不存在")
    await db.delete(edge)
    await db.flush()
    logger.info(
        "dependency_removed",
        request_id=request_id,
        dependency_id=dependency_id,
        by=user_id,
    )


async def list_dependency_edges(
    db: AsyncSession, request_id: str
) -> list[tuple[RequestDependency, Request]]:
    """我依赖谁（边 + 前置需求，按连边时间正序）。"""
    rows = (
        await db.execute(
            select(RequestDependency, Request)
            .join(Request, Request.id == RequestDependency.dependency_id)
            .where(RequestDependency.request_id == request_id)
            .order_by(RequestDependency.created_at)
        )
    ).all()
    return [(edge, target) for edge, target in rows]


async def list_dependent_edges(
    db: AsyncSession, request_id: str
) -> list[tuple[RequestDependency, Request]]:
    """谁依赖我（边 + 后续需求，按连边时间正序）。"""
    rows = (
        await db.execute(
            select(RequestDependency, Request)
            .join(Request, Request.id == RequestDependency.request_id)
            .where(RequestDependency.dependency_id == request_id)
            .order_by(RequestDependency.created_at)
        )
    ).all()
    return [(edge, target) for edge, target in rows]


async def unmet_dependencies(db: AsyncSession, request_id: str) -> list[Request]:
    """未完成前置列表（门禁查询：一次半连接，assign 热路径的唯一新增成本）。

    走 ix_request_dependencies_request_id；空列表 = 放行。
    """
    rows = (
        (
            await db.execute(
                select(Request)
                .join(
                    RequestDependency,
                    RequestDependency.dependency_id == Request.id,
                )
                .where(
                    RequestDependency.request_id == request_id,
                    Request.status.not_in(TERMINAL_STATUSES),
                )
                .order_by(Request.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def assert_not_blocked(db: AsyncSession, request_id: str) -> None:
    """assign 门禁（FR-004）：有未完成前置 → 409 且消息逐条。

    消息超过 5 条截断（「…等 N 个」）——错误消息不是清单 UI，
    完整清单在详情弹层。
    """
    blockers = await unmet_dependencies(db, request_id)
    if not blockers:
        return
    listed = "、".join(f"{b.title}（{b.status}）" for b in blockers[:5])
    if len(blockers) > 5:
        listed += f" 等 {len(blockers)} 个"
    raise DependencyBlockedError(f"有 {len(blockers)} 个前置需求未完成：{listed}")


async def is_blocked(db: AsyncSession, request_id: str) -> bool:
    """`blocked` 布尔（详情/列表端派生字段）。"""
    return len(await unmet_dependencies(db, request_id)) > 0


async def blocked_summaries(
    db: AsyncSession, request_ids: list[str]
) -> dict[str, tuple[int, list[str]]]:
    """批量未完成前置概要（列表端零 N+1：一次 GROUP BY 聚合，data-model §4）。

    返回 {request_id: (未完成数, [标题...])}；无未完成前置的 id 不在结果里。
    标题一并带回是为了卡片徽章的 hover 提示——逐条查才是 N+1，
    一次聚合不是（标题数组只为被挡住的少量行付出体积）。
    """
    if not request_ids:
        return {}
    rows = (
        await db.execute(
            select(
                RequestDependency.request_id,
                Request.title,
            )
            .join(Request, Request.id == RequestDependency.dependency_id)
            .where(
                RequestDependency.request_id.in_(request_ids),
                Request.status.not_in(TERMINAL_STATUSES),
            )
            .order_by(Request.created_at)
        )
    ).all()
    summaries: dict[str, tuple[int, list[str]]] = {}
    for rid, title in rows:
        count, titles = summaries.get(rid, (0, []))
        titles.append(title)
        summaries[rid] = (count + 1, titles)
    return summaries


async def ready_dependents(
    db: AsyncSession, dependency_id: str, exclude_statuses: set[str] | None = None
) -> list[Request]:
    """settle 反查：依赖我刚收敛需求、且**其余前置均已终态**的后续需求。

    exclude_statuses：后续需求需要排除的状态（已终态/running——它们不需要
    「可以启动了」的通知，FR-007 场景 4/5）。

    查询数固定为 2 次（候选一次、其余前置一次），与候选个数无关。
    """
    # R3：原 skip 是手写字面量，还混进了 `"timeout"`——那是 **Run** 状态，
    # `request_status` 枚举里没有这个值。它只是恰好没造成故障（这处是
    # Python 层 `in` 判定而非 SQL 绑定）。现在由状态机导出集合拼装，
    # `timeout` 自然消失。
    skip = exclude_statuses or (TERMINAL_STATUSES | ACTIVE_STATUSES)
    # 候选：直接依赖 dependency_id 的后续需求（一次 IN 查询）
    candidates = list(
        (
            await db.execute(
                select(Request)
                .join(
                    RequestDependency,
                    RequestDependency.request_id == Request.id,
                )
                .where(RequestDependency.dependency_id == dependency_id)
            )
        )
        .scalars()
        .all()
    )
    pending = [c for c in candidates if c.status not in skip]
    if not pending:
        return []

    # 其余前置是否都已终态：一次查出所有候选的全部前置边（含状态），
    # 再在内存里按候选归组判定。
    #
    # 原实现是 `for candidate: await list_dependency_edges(...)` —— 每个候选
    # 一次查询，N 个后续需求就是 N+1 次往返。settle 是终态汇聚点，一次批量
    # 收敛可能解锁几十个后续方，被放大得很明显。
    blocked_ids = {
        rid
        for rid, dep_status in (
            await db.execute(
                select(RequestDependency.request_id, Request.status)
                .join(Request, Request.id == RequestDependency.dependency_id)
                .where(
                    RequestDependency.request_id.in_([c.id for c in pending]),
                    Request.status.not_in(TERMINAL_STATUSES),
                )
            )
        ).all()
    }
    # 有任一未终态前置的候选被排除；其余前置全终态（或无前置）即为就绪。
    return [c for c in pending if c.id not in blocked_ids]


async def user_exists(db: AsyncSession, user_id: str) -> bool:
    return await db.get(User, user_id) is not None
