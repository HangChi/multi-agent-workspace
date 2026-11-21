"""`_authorize_tool_now`：每次调用前的工具授权复核。

这个函数原先作为 `_execute` 的内嵌闭包存在，与一段 SQL 一起埋在 300 行里，
**零测试**。抽出后按四条放行/拒绝规则逐条钉住。

它存在的理由是「快照不是现在的意思」：Run 可能跑几分钟，期间管理员禁用工具、
改 Agent 授权。调度时那份 `tool_grants_snapshot` 只说明开始时允许过，不能
说明现在仍允许。

用真 DB（`db_session`）而不是打桩 `db.execute`：这条 SQL 的 join 条件
（`Tool.enabled.is_(True)` + `Tool.name.in_(CATALOG)`）本身就是被测对象，
打桩等于把要验的东西mock 掉。
"""

from types import SimpleNamespace

import pytest

from app.models.agent import Agent
from app.models.tool import Tool
from app.tasks.execute_run import _authorize_tool_now


def _run(grants: list[str] | None) -> SimpleNamespace:
    return SimpleNamespace(tool_grants_snapshot=grants)


async def _agent_with(db_session, owner, tools: list[str]) -> Agent:
    agent = Agent(
        owner_id=owner.id,
        name=f"auth-{'-'.join(tools) or 'empty'}",
        system_prompt="p",
        model="gpt-4o-mini",
        tools=tools,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


async def _enable(db_session, *names: str, enabled: bool = True) -> None:
    """把目录里的工具置为启用/禁用（`tools` 表是全局开关）。"""
    for name in names:
        existing = await db_session.get(Tool, name)
        if existing is None:
            db_session.add(Tool(name=name, enabled=enabled))
        else:
            existing.enabled = enabled
    await db_session.flush()


async def test_snapshot_and_current_grant_both_required(db_session, make_user) -> None:
    """**两个条件同时满足**才放行：快照里有过、且现在还没被撤销。"""
    owner, _ = await make_user(role="member")
    agent = await _agent_with(db_session, owner, ["read_url"])
    await _enable(db_session, "read_url")
    run = _run(["read_url"])

    assert (
        await _authorize_tool_now(
            db_session, name="read_url", agent=agent, run=run, external_executors={}
        )
        is True
    )


async def test_tool_disabled_after_snapshot_is_denied(db_session, make_user) -> None:
    """快照里有、但工具被管理员全局禁用了 → 必须拒绝。

    这是本函数存在的**主要理由**。只信快照的话，管理员禁用工具后，正在跑的
    Run 会继续拿到授权，直到进程重启。
    """
    owner, _ = await make_user(role="member")
    agent = await _agent_with(db_session, owner, ["read_url"])
    await _enable(db_session, "read_url", enabled=False)
    run = _run(["read_url"])

    assert (
        await _authorize_tool_now(
            db_session, name="read_url", agent=agent, run=run, external_executors={}
        )
        is False
    )


async def test_tool_removed_from_agent_after_snapshot_is_denied(
    db_session, make_user
) -> None:
    """快照里有、但 Agent 的授权被改掉了 → 拒绝。"""
    owner, _ = await make_user(role="member")
    agent = await _agent_with(db_session, owner, [])  # 已被撤销
    await _enable(db_session, "read_url")
    run = _run(["read_url"])  # 快照是旧的

    assert (
        await _authorize_tool_now(
            db_session, name="read_url", agent=agent, run=run, external_executors={}
        )
        is False
    )


async def test_tool_never_in_snapshot_is_denied_even_if_currently_granted(
    db_session, make_user
) -> None:
    """当前授权里有、但快照里没有 → 拒绝。

    方向不能反：Run 一旦开始，授权范围就固定了。执行途中给 Agent 加权限
    不应该让**已经在跑**的 Run 立刻获得新能力（那等于绕过「确认再执行」）。
    """
    owner, _ = await make_user(role="member")
    agent = await _agent_with(db_session, owner, ["read_url"])
    await _enable(db_session, "read_url")
    run = _run([])

    assert (
        await _authorize_tool_now(
            db_session, name="read_url", agent=agent, run=run, external_executors={}
        )
        is False
    )


async def test_none_snapshot_grants_nothing(db_session, make_user) -> None:
    """`tool_grants_snapshot` 为 None（老数据）时按「没有授权」处理，不放行。"""
    owner, _ = await make_user(role="member")
    agent = await _agent_with(db_session, owner, ["read_url"])
    await _enable(db_session, "read_url")

    assert (
        await _authorize_tool_now(
            db_session,
            name="read_url",
            agent=agent,
            run=_run(None),
            external_executors={},
        )
        is False
    )


async def test_no_agent_denies_everything(db_session) -> None:
    """无 Agent（组父 Run 编排占位）没有可授权的工具。"""
    assert (
        await _authorize_tool_now(
            db_session,
            name="read_url",
            agent=None,
            run=_run(["read_url"]),
            external_executors={},
        )
        is False
    )


async def test_external_tool_is_authorized_here_but_rechecked_inside(
    db_session,
) -> None:
    """外部工具在这里直接放行，因为它自己会在每次 execute 前复核。

    这条不是「少查一次」的偷懒：外部工具的复核要看 provider 状态、授权行、
    凭据有效期，比这里的「名字在不在」细得多。在这里重复一遍只会得到更弱的
    结论，还让两处逻辑容易分叉。
    """
    assert (
        await _authorize_tool_now(
            db_session,
            name="mcp_search_v1",
            agent=None,  # 连 Agent 都不需要
            run=_run(None),
            external_executors={"mcp_search_v1": object()},
        )
        is True
    )


async def test_unknown_tool_name_is_denied(db_session, make_user) -> None:
    """名字既不在快照也不在目录 → 拒绝（不抛异常）。"""
    owner, _ = await make_user(role="member")
    agent = await _agent_with(db_session, owner, ["read_url"])
    await _enable(db_session, "read_url")

    assert (
        await _authorize_tool_now(
            db_session,
            name="no_such_tool",
            agent=agent,
            run=_run(["read_url", "no_such_tool"]),
            external_executors={},
        )
        is False
    )


@pytest.mark.parametrize("tool_name", ["read_url", "calculate"])
async def test_multiple_grants_are_independent(
    db_session, make_user, tool_name
) -> None:
    """授权是逐工具的：放行一个不影响另一个。"""
    owner, _ = await make_user(role="member")
    agent = await _agent_with(db_session, owner, ["read_url", "calculate"])
    await _enable(db_session, "read_url", "calculate")
    run = _run(["read_url", "calculate"])

    assert (
        await _authorize_tool_now(
            db_session,
            name=tool_name,
            agent=agent,
            run=run,
            external_executors={},
        )
        is True
    )
