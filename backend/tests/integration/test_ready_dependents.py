"""`ready_dependents` 与 `_adjacency_for` 的收敛（R3 观察项）。

两个函数各自有一个「不报错但会劣化」的问题，普通结果断言抓不住：

1. `ready_dependents` 的 skip 集合是手写字面量，混进了 **Run** 状态
   `"timeout"`（`request_status` 枚举里没有这个值）。它只因为判定发生在
   Python 层才没炸——一旦有人把它挪进 SQL `not_in` 就会 `invalid input value
   for enum`。同时它对每个候选各查一次前置边（N+1）。
2. `_adjacency_for` 无条件 `SELECT` 整张依赖表，每次加边都拉全库。

这里用两条独立的证据线：
- 语义线：`timeout` 不再被当作需求状态的合法取值；
- 成本线：数 SELECT 的次数，与候选/连线规模解耦。
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from app.models.request import Request
from app.models.request_dependency import RequestDependency
from app.models.user import User
from app.orchestrator.request_state_machine import RequestStatus
from app.services import dependency_service

pytestmark = pytest.mark.integration


@contextmanager
def _select_counter(db_session):
    """数本会话发出的 SELECT 条数（N+1 只能靠查询次数证明，结果断言看不见）。"""
    counter = {"n": 0}

    def _on_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    # 挂在 sync bind 上（与 test_schedule_list_query_count.py 一致）：
    # AsyncSession.get_bind() 拿到的不一定是带 sync_engine 的对象。
    bind = db_session.sync_session.bind
    event.listen(bind, "before_cursor_execute", _on_execute)
    try:
        yield counter
    finally:
        event.remove(bind, "before_cursor_execute", _on_execute)


async def _user(db) -> User:
    user = User(
        email=f"rd-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _request(db, owner: User, title: str, status: str = "created") -> Request:
    req = Request(owner_id=owner.id, title=title, status=status)
    db.add(req)
    await db.commit()
    return req


async def _edge(db, dependent: Request, prerequisite: Request) -> None:
    db.add(
        RequestDependency(
            request_id=dependent.id,
            dependency_id=prerequisite.id,
            created_by=dependent.owner_id,
        )
    )
    await db.commit()


# ---------- 语义：skip 集合不再混入 Run 状态 ----------


def test_skip_set_contains_no_run_only_status():
    """`timeout` 是 Run 状态，连 RequestStatus 都解析不了（R3）。

    反向验证：把 skip 改回原来的手写字面量（含 `"timeout"`），本用例变红。
    """
    assert "timeout" not in dependency_service.TERMINAL_STATUSES
    with pytest.raises(ValueError):
        RequestStatus("timeout")

    # 每一个「需求终态」都必须是枚举合法值——这保证集合可以被安全地绑进 SQL。
    for status in dependency_service.TERMINAL_STATUSES:
        assert RequestStatus(status) in {
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
        }


async def test_ready_dependents_ignores_run_status_word(db_session):
    """`ready_dependents` 的默认排除集不含任何 Run 专有状态。"""
    owner = await _user(db_session)
    prerequisite = await _request(db_session, owner, "前置", status="completed")
    dependent = await _request(db_session, owner, "后续", status="created")
    await _edge(db_session, dependent, prerequisite)

    ready = await dependency_service.ready_dependents(db_session, prerequisite.id)
    assert [r.id for r in ready] == [dependent.id]


async def test_dependent_with_unmet_prerequisite_is_not_ready(db_session):
    """语义正确性：其余前置未终态 → 不算就绪（别为了降查询数把逻辑改错）。"""
    owner = await _user(db_session)
    prerequisite = await _request(db_session, owner, "已终态前置", status="completed")
    blocker = await _request(db_session, owner, "未完成前置", status="running")
    dependent = await _request(db_session, owner, "后续", status="created")
    await _edge(db_session, dependent, prerequisite)
    await _edge(db_session, dependent, blocker)

    ready = await dependency_service.ready_dependents(db_session, prerequisite.id)
    assert ready == []


async def test_running_dependent_is_skipped(db_session):
    """running 的后续方不需要「可以启动了」的通知（FR-007 场景 4）。"""
    owner = await _user(db_session)
    prerequisite = await _request(db_session, owner, "前置", status="completed")
    dependent = await _request(db_session, owner, "后续", status="running")
    await _edge(db_session, dependent, prerequisite)

    ready = await dependency_service.ready_dependents(db_session, prerequisite.id)
    assert ready == []


# ---------- 成本：候选数不放大查询次数 ----------


async def test_query_count_is_flat_in_dependent_count(db_session):
    """N 个后续需求不再产生 N+1 次查询。

    原实现 `for candidate: await list_dependency_edges(...)`，每个候选一次
    JOIN 查询。10 个后续方 → 1 + 10 = 11 条 SELECT。

    `settle` 是终态汇聚点，一次收敛可能解锁几十个后续方，放大得很明显。

    反向验证：把 `ready_dependents` 换回逐候选版本，实测 11 次，本用例变红。
    """
    owner = await _user(db_session)
    prerequisite = await _request(db_session, owner, "公共前置", status="completed")
    # 每条后续需求再挂一个已终态的前置，确保「其余前置」分支真的被走到。
    extra = await _request(db_session, owner, "另一前置", status="completed")

    dependents = []
    for i in range(10):
        d = await _request(db_session, owner, f"后续{i}", status="created")
        await _edge(db_session, d, prerequisite)
        await _edge(db_session, d, extra)
        dependents.append(d)

    db_session.expunge_all()
    with _select_counter(db_session) as counter:
        ready = await dependency_service.ready_dependents(db_session, prerequisite.id)

    assert {r.id for r in ready} == {d.id for d in dependents}
    # 2 条：候选一次 + 全部候选的前置一次。留一点余量给会话级探测查询，
    # 但不能随候选数增长（旧实现是 11 条）。
    assert counter["n"] <= 3, f"查询数 {counter['n']} 次，疑似 N+1 回归"


# ---------- `_adjacency_for` 连通域收窄 ----------


async def test_adjacency_only_reads_the_connected_component(db_session):
    """邻接表不再整表加载：域外的边不该出现在结果里。

    原实现无条件 `SELECT request_id, dependency_id`，别人的依赖图也会被读出来。
    反向验证：换回全表扫的实现，`x`/`y` 会出现在邻接表中，本用例变红。
    """
    owner = await _user(db_session)
    a = await _request(db_session, owner, "A")
    b = await _request(db_session, owner, "B")
    c = await _request(db_session, owner, "C")
    # 无关连通域
    x = await _request(db_session, owner, "X")
    y = await _request(db_session, owner, "Y")

    await _edge(db_session, b, a)  # B 依赖 A
    await _edge(db_session, c, b)  # C 依赖 B
    await _edge(db_session, y, x)  # X/Y 与本域无关

    adjacency = await dependency_service._adjacency_for(db_session, b.id)

    assert adjacency == {b.id: {a.id}, c.id: {b.id}}
    assert x.id not in adjacency and y.id not in adjacency


async def test_adjacency_still_detects_cycles(db_session):
    """收窄不能破坏成环判定（这是 `_adjacency_for` 存在的唯一理由）。

    已有边：B 依赖 A（B→A）、C 依赖 B（C→B），即 A ← B ← C。
    `would_create_cycle(adj, request_id, dependency_id)` 问的是「加一条
    request_id 依赖 dependency_id 的边会不会闭合环」——从 dependency_id
    沿既有边找 request_id。
    """
    owner = await _user(db_session)
    a = await _request(db_session, owner, "A")
    b = await _request(db_session, owner, "B")
    c = await _request(db_session, owner, "C")
    await _edge(db_session, b, a)
    await _edge(db_session, c, b)

    adjacency = await dependency_service._adjacency_for(db_session, c.id)

    # 加「A 依赖 C」→ 从 C 出发能走到 B、再走到 A → 成环。
    assert dependency_service.would_create_cycle(adjacency, a.id, c.id) is True
    # 加「B 依赖 C」→ 从 C 出发走到 B → 成环。
    assert dependency_service.would_create_cycle(adjacency, b.id, c.id) is True
    # 顺链方向（C 依赖 A，即补一条早已隐含的传递边）不成环。
    assert dependency_service.would_create_cycle(adjacency, c.id, a.id) is False
