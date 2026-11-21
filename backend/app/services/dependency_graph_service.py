"""需求依赖图谱（021）：下游子图 + 拓扑序 + 关键路径。

009 的依赖只有「加边/删边/门禁/解锁通知」，没有「整条链长什么样」的视图。
021 补这个视图，并在同一份子图上算两件流水线真正关心的事：

- **拓扑序**：按依赖先后排好的执行顺序（前置在前）。有环时返回已排序部分 +
  `has_cycle=True`（009 的加边已拒绝成环，这里是防御，不当作正常路径）。
- **关键路径**：子图里最长的一条依赖链（边权 1 = 按「跳数」算）。没有工期数据，
  用跳数近似「这条链最慢」；将来有了实际耗时再换权重。

为什么要限制节点数：一个项目的依赖图可能上千节点，前端渲染与一次往返都不该
无限增长。超过上限时返回 `truncated=True`，由 UI 提示「只显示了前 N 个」。
"""

from __future__ import annotations

from collections import defaultdict, deque

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request import Request
from app.models.request_dependency import RequestDependency

MAX_NODES = 300
TERMINAL = {"completed", "failed", "cancelled"}


async def downstream_graph(
    db: AsyncSession, root_id: str, *, max_nodes: int = MAX_NODES
) -> dict:
    """以 root 为起点的下游子图（谁依赖我 → 谁又依赖它…）。"""
    root = await db.get(Request, root_id)
    if root is None:
        # 归属校验由端点负责（它先 get_request），这里只防御
        return {
            "root_id": root_id,
            "nodes": [],
            "edges": [],
            "topo_order": [],
            "critical_path": [],
            "has_cycle": False,
            "truncated": False,
        }

    node_ids: list[str] = [root_id]
    seen = {root_id}
    frontier = [root_id]
    edges: list[tuple[str, str]] = []  # (依赖方, 被依赖方)
    truncated = False

    while frontier and not truncated:
        rows = (
            await db.execute(
                select(
                    RequestDependency.request_id, RequestDependency.dependency_id
                ).where(RequestDependency.dependency_id.in_(frontier))
            )
        ).all()
        next_frontier: list[str] = []
        for dependent_id, dependency_id in rows:
            edges.append((dependent_id, dependency_id))
            if dependent_id in seen:
                continue
            if len(node_ids) >= max_nodes:
                truncated = True
                break
            seen.add(dependent_id)
            node_ids.append(dependent_id)
            next_frontier.append(dependent_id)
        frontier = next_frontier

    requests = {
        r.id: r
        for r in (
            (await db.execute(select(Request).where(Request.id.in_(node_ids))))
            .scalars()
            .all()
        )
    }
    # 子图内的未完成前置数（含子图外的前置——「我被挡住」是全局事实）
    unmet = dict(
        (
            await db.execute(
                select(RequestDependency.request_id, func.count())
                .join(Request, Request.id == RequestDependency.dependency_id)
                .where(
                    RequestDependency.request_id.in_(node_ids),
                    Request.status.not_in(TERMINAL),
                )
                .group_by(RequestDependency.request_id)
            )
        ).all()
    )

    sub_edges = [(a, b) for a, b in edges if a in seen and b in seen]
    topo, has_cycle = _topological_order(node_ids, sub_edges)
    critical = _critical_path(node_ids, sub_edges)

    return {
        "root_id": root_id,
        "nodes": [
            {
                "id": nid,
                "title": requests[nid].title,
                "status": requests[nid].status,
                "owner_id": requests[nid].owner_id,
                "assignee_id": requests[nid].assignee_id,
                "auto_start": bool(requests[nid].auto_start),
                "unmet_count": int(unmet.get(nid, 0)),
            }
            for nid in node_ids
            if nid in requests
        ],
        "edges": [{"dependent_id": a, "dependency_id": b} for a, b in sub_edges],
        "topo_order": topo,
        "critical_path": critical,
        "has_cycle": has_cycle,
        "truncated": truncated,
    }


def _topological_order(
    node_ids: list[str], edges: list[tuple[str, str]]
) -> tuple[list[str], bool]:
    """Kahn：前置在前。返回 (顺序, 是否有环)。

    边 (a, b) 表示 a 依赖 b，所以 b 必须先出现 → 出边 b → a。
    """
    indegree = {nid: 0 for nid in node_ids}
    successors: dict[str, list[str]] = defaultdict(list)
    for dependent_id, dependency_id in edges:
        if dependent_id == dependency_id:
            continue
        successors[dependency_id].append(dependent_id)
        indegree[dependent_id] = indegree.get(dependent_id, 0) + 1

    queue = deque([nid for nid in node_ids if indegree.get(nid, 0) == 0])
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for nxt in successors.get(current, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return order, len(order) != len(node_ids)


def _critical_path(node_ids: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """子图内最长依赖链（按跳数）。返回节点 id 序列。"""
    if not node_ids:
        return []
    order, has_cycle = _topological_order(node_ids, edges)
    if has_cycle:
        return []

    successors: dict[str, list[str]] = defaultdict(list)
    for dependent_id, dependency_id in edges:
        successors[dependency_id].append(dependent_id)

    dist = {nid: 0 for nid in node_ids}
    prev: dict[str, str | None] = {nid: None for nid in node_ids}
    for nid in order:
        for nxt in successors.get(nid, []):
            if dist[nid] + 1 > dist[nxt]:
                dist[nxt] = dist[nid] + 1
                prev[nxt] = nid

    end = max(node_ids, key=lambda n: dist.get(n, 0))
    path: list[str] = []
    cursor: str | None = end
    while cursor is not None:
        path.append(cursor)
        cursor = prev.get(cursor)
    path.reverse()
    return path if len(path) > 1 else []
