"""009 成环检测单元测试（T006）：BFS 纯函数，不碰数据库。

为什么值得单测：成环检测是整个特性唯一「放行一个就死锁一条链」的判定
（spec SC-004），且它的正确性只依赖图结构——纯函数测试比集成测试
更快更准。集成测试（test_dependencies.py D-5/D-6）负责端到端，
这里钉算法本身。

函数签名：`would_create_cycle(edges, request_id, dependency_id)`。
edges 是既有边的 (request_id, dependency_id) 邻接表。
"""

from app.services.dependency_service import would_create_cycle


def _adjacency(*pairs: tuple[str, str]) -> dict[str, set[str]]:
    """(被依赖方, 前置) 对 → 邻接表（被依赖方 → 其前置集合）。"""
    graph: dict[str, set[str]] = {}
    for request_id, dependency_id in pairs:
        graph.setdefault(request_id, set()).add(dependency_id)
    return graph


class TestWouldCreateCycle:
    def test_empty_graph_allows_any_edge(self):
        assert would_create_cycle({}, "B", "A") is False

    def test_self_dependency_is_cycle(self):
        # 自环：B 依赖 B。service 层先拦（400），这里也要判真兜底
        assert would_create_cycle({}, "B", "B") is True

    def test_direct_cycle(self):
        # A 依赖 B 已存在 → 给 B 加「依赖 A」成环
        edges = _adjacency(("A", "B"))
        assert would_create_cycle(edges, "B", "A") is True

    def test_transitive_cycle_two_hops(self):
        # 链 A→B→C（A 依赖 B，B 依赖 C）。给 C 加「依赖 A」：
        # 从 A 出发经 B 可达 C，新边 C→A 闭合环。方向写反的顺链加边
        # （A 依赖 C）是冗余传递边，不成环——一并钉住，防实现退化。
        edges = _adjacency(("A", "B"), ("B", "C"))
        assert would_create_cycle(edges, "C", "A") is True
        assert would_create_cycle(edges, "A", "C") is False

    def test_transitive_cycle_three_hops(self):
        # 链 A→B→C→D，给 D 加「依赖 A」（三跳环）
        edges = _adjacency(("A", "B"), ("B", "C"), ("C", "D"))
        assert would_create_cycle(edges, "D", "A") is True

    def test_diamond_is_not_cycle(self):
        # 菱形：B 依赖 A 与 C，A、C 都依赖 D。给 D 加「依赖 B」才成环；
        # 而 B 依赖 D 本身（已存在）不重复判定——这里验证合法形状放行
        edges = _adjacency(("B", "A"), ("B", "C"), ("A", "D"), ("C", "D"))
        assert would_create_cycle(edges, "D", "B") is True  # 反向才成环
        assert would_create_cycle(edges, "B", "D") is False  # 已存在的方向

    def test_disconnected_components(self):
        # 两张互不相连的图，跨图连边不成环
        edges = _adjacency(("A", "B"), ("C", "D"))
        assert would_create_cycle(edges, "A", "D") is False

    def test_wide_graph_performance_sanity(self):
        # 100 节点链 + 分叉：BFS 应在毫秒级（防退化成指数搜索）
        pairs = [(f"n{i}", f"n{i + 1}") for i in range(99)]
        edges = _adjacency(*pairs)
        assert would_create_cycle(edges, "n0", "n99") is False  # 顺链放行
        assert would_create_cycle(edges, "n99", "n0") is True  # 反链成环

    def test_visited_set_prevents_revisit(self):
        # 菱形会重复到达 D——visited 集合保证不重复展开（正确性+有界）
        edges = _adjacency(("B", "A"), ("B", "C"), ("A", "D"), ("C", "D"), ("D", "E"))
        assert would_create_cycle(edges, "E", "B") is True
