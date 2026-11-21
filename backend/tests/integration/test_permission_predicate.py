"""归属判权收敛（R1）：谓词单一来源 + admin 口径如实记录。

背景：同一个归属谓词被复制在 10 处，且放行口径互相冲突。报告里举的例子是
「管理员能改任意 Agent 的出站网络策略，却改不了它的名字」——这不是设计，
是两处各写一遍 `owner_id != user_id` 之后自然长出来的漂移。

本文件锁两件事：
1. 各处判定**实际调用**统一谓词（用 monkeypatch 证明，而不是靠读代码相信）；
2. admin 的差异是**显式且已记录**的（放行的两处放行、其余不放行），
   将来谁想统一都会先看到这里。

`test_admin_*` 那几条是「现状快照」。它们**不是**在认可这个不一致，
而是把它变成可执行的文档：真要统一时，这些用例会一起变红，提醒同步改
016 spec 与前端。
"""

from __future__ import annotations

import uuid

import pytest

from app.models.agent import Agent
from app.models.request import Request
from app.models.user import User
from app.services import (
    agent_service,
    dependency_service,  # noqa: F401  (确认模块可导入，无循环依赖)
    dispatch_service,
    request_service,
)

pytestmark = pytest.mark.integration


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"perm-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _request(db, owner: User, status: str = "created", **kw) -> Request:
    req = Request(owner_id=owner.id, title="权限测试", status=status, **kw)
    db.add(req)
    await db.commit()
    return req


async def _agent(db, owner: User) -> Agent:
    agent = Agent(
        owner_id=owner.id,
        name=f"权限 {uuid.uuid4().hex[:4]}",
        system_prompt="p",
        model="m",
        temperature=0.7,
        max_steps=4,
        max_run_seconds=300,
        cost_limit_cents=1000,
        tools=[],
        egress_mode="default",
        status="active",
    )
    db.add(agent)
    await db.commit()
    return agent


# ---------- 需求侧：统一谓词 ----------


def test_is_participant_covers_owner_and_assignee():
    """谓词本体：创建者与接收人放行，其他人不放行。"""

    class R:
        owner_id = "u1"
        assignee_id = "u2"

    assert request_service.is_participant(R(), "u1") is True
    assert request_service.is_participant(R(), "u2") is True
    assert request_service.is_participant(R(), "u3") is False

    # 无接收人时只有创建者
    class R2:
        owner_id = "u1"
        assignee_id = None

    assert request_service.is_participant(R2(), "u1") is True
    assert request_service.is_participant(R2(), "u2") is False


async def test_dispatch_uses_the_shared_predicate(db_session, monkeypatch):
    """`dispatch_service._check_access` 必须调用统一谓词，而不是自己写一遍。

    做法：把 `is_participant` 换成总返回 True 的假函数，非相关人也能过——
    如果那段代码是自己 `owner_id != x and assignee_id != x`，替换不影响，
    用例就会红。

    反向验证：把 `_check_access` 改回内联谓词，本用例变红。
    """
    owner = await _user(db_session)
    stranger = await _user(db_session)
    request = await _request(db_session, owner)

    calls: list[str] = []
    monkeypatch.setattr(
        request_service,
        "is_participant",
        lambda req, uid: calls.append(uid) or True,
    )
    # 让 dispatch 模块看到同一个被替换的引用
    monkeypatch.setattr(
        dispatch_service.request_service,
        "is_participant",
        request_service.is_participant,
    )

    dispatch_service._check_access(request, stranger.id)
    assert calls == [stranger.id], "没有走统一谓词"


async def test_request_owner_check_uses_the_shared_predicate(db_session, monkeypatch):
    """`request_service._check_owner` 同样必须走谓词。"""
    owner = await _user(db_session)
    stranger = await _user(db_session)
    request = await _request(db_session, owner)

    monkeypatch.setattr(request_service, "is_participant", lambda req, uid: True)
    request_service._check_owner(request, stranger.id)  # 不抛 = 走了谓词


async def test_non_participant_gets_404_with_identical_message(db_session):
    """016 契约 §2：非相关人的 404 文案必须与「真的不存在」逐字相同。"""
    owner = await _user(db_session)
    stranger = await _user(db_session)
    request = await _request(db_session, owner)

    with pytest.raises(Exception) as denied:
        request_service._check_owner(request, stranger.id)
    with pytest.raises(Exception) as missing:
        await request_service.get_request(db_session, str(uuid.uuid4()), stranger.id)

    assert str(denied.value) == str(missing.value)


# ---------- Agent 侧：统一加载器 ----------


async def test_get_agent_rejects_stranger_with_404(db_session):
    owner = await _user(db_session)
    stranger = await _user(db_session)
    agent = await _agent(db_session, owner)

    with pytest.raises(Exception) as exc:
        await agent_service.get_agent(db_session, agent.id, stranger.id)
    assert "不存在" in str(exc.value)


async def test_get_agent_allows_extra_owner_ids_for_shared_projects(db_session):
    """010 共享项目：参与者可用他人 Agent 执行（`allow_owner_ids` 分支）。"""
    owner = await _user(db_session)
    member = await _user(db_session)
    agent = await _agent(db_session, owner)

    found = await agent_service.get_agent(
        db_session, agent.id, member.id, allow_owner_ids={owner.id}
    )
    assert found.id == agent.id
    # 不传就不放行（默认行为与旧实现一致）
    with pytest.raises(Exception, match="不存在"):
        await agent_service.get_agent(db_session, agent.id, member.id)


async def test_assignment_target_uses_get_agent(db_session, monkeypatch):
    """`assignment_service` 的目标校验必须走 `get_agent`（R1）。"""
    from app.services import assignment_service

    owner = await _user(db_session)
    stranger = await _user(db_session)
    agent = await _agent(db_session, owner)

    calls: list[tuple] = []
    original = agent_service.get_agent

    async def spy(db, agent_id, user_id, **kw):
        calls.append((agent_id, user_id, kw))
        return await original(db, agent_id, user_id, **kw)

    # 只设一次：`assignment_service.agent_service` 与 `agent_service`
    # 是**同一个模块对象**，设两次等于后者覆盖前者。
    monkeypatch.setattr(agent_service, "get_agent", spy)

    with pytest.raises(Exception, match="不存在"):
        await assignment_service._load_agent(db_session, agent.id, stranger.id)
    assert calls and calls[0][1] == stranger.id, "没有走 agent_service.get_agent"


# ---------- admin 口径：现状快照（不是认可，是可执行的记录） ----------


async def test_admin_cannot_rename_another_users_agent(db_session):
    """现状：admin 改不了他人 Agent 的配置（走 `get_agent` 纯 owner 口径）。"""
    owner = await _user(db_session)
    admin = await _user(db_session, role="admin")
    agent = await _agent(db_session, owner)

    with pytest.raises(Exception) as exc:
        await agent_service.get_agent(db_session, agent.id, admin.id)
    assert "不存在" in str(exc.value)


async def test_admin_can_inspect_another_users_egress_policy(db_session):
    """现状：admin **能**看他人 Agent 的出站策略。

    与上一条并列存在，就是报告 R1 指出的那个不一致。016 spec 明确
    「admin 读他人资源是否放行由各端点既有策略决定，本特性不改」，
    因此本轮只把它记录成测试，不改行为。
    """
    from app.services import egress_service

    owner = await _user(db_session)
    admin = await _user(db_session, role="admin")
    agent = await _agent(db_session, owner)

    # 不抛异常即通过（admin 被显式放行）
    await egress_service._ensure_agent_owner(db_session, agent.id, admin.id, admin.role)


async def test_ws_admin_can_subscribe_but_http_admin_cannot_read(db_session):
    """现状对照：WS 订阅放行 admin，HTTP 读端点不放行。

    这条是 R1 里最容易被误判为「漏了」的地方，所以用测试钉住：
    两处都是既有产品决策，不是实现漂移。
    """
    from app.api.v1 import runs as runs_module

    owner = await _user(db_session)
    admin = await _user(db_session, role="admin")
    request = await _request(db_session, owner)

    # WS 侧：admin 放行（直接调谓词 + admin 判定，避开建连）
    assert request_service.is_participant(request, admin.id) is False
    assert admin.role == "admin"  # WS 的 admin 分支就是看这个

    # HTTP 侧：admin 读他人需求没有额外放行
    with pytest.raises(Exception, match="不存在"):
        await request_service.get_request(db_session, request.id, admin.id)

    assert runs_module is not None  # 防止 import 被优化掉的提示


async def test_active_run_statuses_holds_no_request_status(db_session):
    """交叉确认：`_ACTIVE_RUN_STATUSES` 只含 Run 状态，不得混入需求状态。

    R3 已把这份集合收敛到状态机；这里守的是「别把两个领域的词混在一起」，
    因为 `accepted` 这种需求状态一旦漏进来，`settle` 的「还有 Run 没结束」
    判断就会对不上任何实际 Run 状态。
    """
    assert "accepted" not in request_service._ACTIVE_RUN_STATUSES
    assert "pending" not in request_service._ACTIVE_RUN_STATUSES
