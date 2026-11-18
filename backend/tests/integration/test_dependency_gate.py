"""009 分配门禁与解锁通知集成测试（T013，US2/US3/FR-004~009）。

四条硬行为：
1. 未完成前置 → assign 409 且消息**逐条**列出（SC-001）；
2. 三种终态（completed/failed/cancelled）都解锁（FR-006/R-01）；
3. 拖员工（user 落点）不查门禁（FR-005）——责任流转与执行启动两回事；
4. 调度自动分配撞门禁 → 建单不分配（FR-009/R-07，不走 failed 路径）。
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.request import Request
from app.models.run import Run
from app.models.user import User

pytestmark = pytest.mark.integration


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"gate-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _agent(db, owner: User) -> dict:
    """直接建 Agent 行（不走 API，省一轮注册）。"""
    from app.models.agent import Agent

    agent = Agent(
        owner_id=owner.id,
        name=f"门禁验收 {uuid.uuid4().hex[:4]}",
        system_prompt="你是验收助手。",
        model="gpt-4o-mini",
        max_steps=4,
        cost_limit_cents=10000,
        tools=[],
        status="active",
    )
    db.add(agent)
    await db.commit()
    return agent


async def _make_request(db, owner: User, title: str, **kw) -> Request:
    req = Request(owner_id=owner.id, title=title, **kw)
    db.add(req)
    await db.commit()
    return req


async def _edge(db, dependent: Request, prerequisite: Request) -> None:
    from app.models.request_dependency import RequestDependency

    db.add(
        RequestDependency(
            request_id=dependent.id,
            dependency_id=prerequisite.id,
            created_by=dependent.owner_id,
        )
    )
    await db.commit()


# ---------- 1. 门禁挡住 + 消息逐条 ----------


async def test_assign_blocked_lists_every_blocker(db_session, client, make_headers):
    """SC-001：未完成前置 → 409 且消息逐条列出（标题+状态）。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    agent = await _agent(db_session, owner)
    blocker1 = await _make_request(db_session, owner, "前置一", status="running")
    blocker2 = await _make_request(db_session, owner, "前置二", status="created")
    done = await _make_request(db_session, owner, "已完成的前置", status="completed")
    dependent = await _make_request(db_session, owner, "后续")
    await _edge(db_session, dependent, blocker1)
    await _edge(db_session, dependent, blocker2)
    await _edge(db_session, dependent, done)

    resp = await client.post(
        f"/api/v1/requests/{dependent.id}/assign",
        json={"assignee_type": "agent", "assignee_id": agent.id},
        headers=headers,
    )
    assert resp.status_code == 409
    message = resp.json()["error"]["message"]
    assert "前置一" in message and "前置二" in message
    assert "已完成的前置" not in message  # 已完成的不出现
    assert "2 个前置需求未完成" in message


async def test_assign_allowed_after_terminal(db_session, client, make_headers):
    """FR-006：三种终态都解锁（completed/failed/cancelled）。"""
    from app.services.request_service import settle_request_status

    owner = await _user(db_session)
    headers = await make_headers(owner)
    agent = await _agent(db_session, owner)

    for terminal in ("completed", "failed", "cancelled"):
        pre = await _make_request(
            db_session, owner, f"前置-{terminal}", status="running"
        )
        dependent = await _make_request(db_session, owner, f"后续-{terminal}")
        await _edge(db_session, dependent, pre)
        # 配一条同状态终态 Run：settle 的契约是「只处理 running 的需求」，
        # 终态由它按名下 Run 的实际结果算出——直接改 request.status 会
        # 绕过收敛路径（也就测不到通知逻辑）。
        db_session.add(
            Run(
                request_id=pre.id,
                owner_id=owner.id,
                assignee_type="agent",
                assignee_id=str(uuid.uuid4()),
                strategy="direct",
                status=terminal,
            )
        )
        await db_session.commit()
        assert await settle_request_status(db_session, pre.id) == terminal
        await db_session.commit()  # API 独立 session，看不到未提交的终态

        resp = await client.post(
            f"/api/v1/requests/{dependent.id}/assign",
            json={"assignee_type": "agent", "assignee_id": agent.id},
            headers=headers,
        )
        assert resp.status_code == 202, f"{terminal} 应解锁，实得 {resp.status_code}"


# ---------- 3. user 落点不查门禁 ----------


async def test_user_dispatch_not_gated(db_session, client, make_headers):
    """FR-005：拖员工（转交责任）不查依赖——员工可以先接收一个还被挡着的需求。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    headers = await make_headers(owner)
    blocker = await _make_request(db_session, owner, "未完成前置", status="running")
    dependent = await _make_request(db_session, owner, "后续")
    await _edge(db_session, dependent, blocker)

    resp = await client.post(
        f"/api/v1/requests/{dependent.id}/assign",
        json={"assignee_type": "user", "assignee_id": worker.id},
        headers=headers,
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"


# ---------- 4. 调度撞门禁：建单不分配 ----------


async def test_schedule_gate_creates_without_assign(
    db_session, client, make_headers, monkeypatch
):
    """FR-009/R-07：调度自动分配撞门禁 → 需求照样建、无 Run、
    trigger=fired + reason 注明；不走 failed 路径（那条路连需求一起回滚）。

    触发窗口说明：调度是「建单 → 立刻 assign」一个事务，正常情况下依赖边
    来不及连上（边要求需求先存在）。真实窗口是 flush 与 assign 之间的竞态
    （并发人工连边）。这里用包装 assign 的方式**确定性地复现该竞态**——
    比跳过测试诚实，也钉住了行为本身。
    """
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from app.models.schedule import Schedule
    from app.services import assignment_service, schedule_service

    owner = await _user(db_session)
    agent = await _agent(db_session, owner)
    blocker = await _make_request(db_session, owner, "阻塞前置", status="running")

    schedule = Schedule(
        owner_id=owner.id,
        name=f"调度门禁 {uuid.uuid4().hex[:4]}",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        template_title="调度建的需求",
        template_priority="normal",
        target_type="agent",
        target_id=agent.id,
        enabled=True,
        next_run_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(schedule)
    await db_session.commit()

    original_assign = assignment_service.assign

    async def _assign_with_race(db, **kwargs):
        """在 assign 读需求之前连上边——复现 flush→assign 窗口的人工连边。"""
        from app.models.request_dependency import RequestDependency

        db.add(
            RequestDependency(
                request_id=kwargs["request_id"],
                dependency_id=blocker.id,
                created_by=owner.id,
            )
        )
        await db.flush()
        return await original_assign(db, **kwargs)

    with patch.object(assignment_service, "assign", _assign_with_race):
        trigger = await schedule_service.fire_schedule(db_session, schedule)
    await db_session.commit()

    assert trigger is not None
    assert trigger.result == "fired"  # 不是 failed
    assert trigger.reason and "前置" in trigger.reason
    second = await db_session.get(Request, trigger.request_id)
    assert second is not None  # 需求建出来了（没被回滚）
    runs = (
        (await db_session.execute(select(Run).where(Run.request_id == second.id)))
        .scalars()
        .all()
    )
    assert runs == []  # 门禁挡下，没有分配


# ---------- 2. 解锁通知（US3/FR-007/008） ----------


async def test_ready_notification_on_settle(db_session, client, make_headers):
    """前置终态 → 后续方 owner 收到 dependency_ready；幂等不重发。"""
    from app.services.request_service import settle_request_status

    owner = await _user(db_session)
    worker = await _user(db_session)
    pre = await _make_request(db_session, owner, "前置需求", status="running")
    dependent = await _make_request(
        db_session, owner, "后续需求", assignee_id=worker.id, status="pending"
    )
    await _edge(db_session, dependent, pre)

    db_session.add(
        Run(
            request_id=pre.id,
            owner_id=owner.id,
            assignee_type="agent",
            assignee_id=str(uuid.uuid4()),
            strategy="direct",
            status="completed",
        )
    )
    await db_session.commit()
    assert await settle_request_status(db_session, pre.id) == "completed"
    # autoflush=False：settle 只 db.add 没 flush，查询前必须 commit
    # （本特性首轮就在这里挂过——通知发了但 SELECT 看不到）
    await db_session.commit()

    # owner 与 assignee（非同一人）各一条
    owner_notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == owner.id,
                    Notification.type == "dependency_ready",
                )
            )
        )
        .scalars()
        .all()
    )
    worker_notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == worker.id,
                    Notification.type == "dependency_ready",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(owner_notes) == 1
    assert len(worker_notes) == 1
    assert owner_notes[0].payload["dependency_title"] == "前置需求"
    assert owner_notes[0].payload["dependency_status"] == "completed"
    assert owner_notes[0].request_id == dependent.id  # 点通知跳到后续方

    # 幂等：再次收敛（已终态，settle 直接返回 None）不重发
    assert await settle_request_status(db_session, pre.id) is None
    again = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == owner.id,
                    Notification.type == "dependency_ready",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(again) == 1


async def test_no_notification_when_dependent_already_terminal(
    db_session, client, make_headers
):
    """FR-007 场景 4：后续方已终态 → 不发通知（它已经不需要启动了）。"""
    from app.services.request_service import settle_request_status

    owner = await _user(db_session)
    pre = await _make_request(db_session, owner, "前置", status="running")
    dependent = await _make_request(
        db_session, owner, "已收尾的后续", status="completed"
    )
    await _edge(db_session, dependent, pre)

    pre.status = "completed"
    await db_session.commit()
    await settle_request_status(db_session, pre.id)
    await db_session.commit()

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == owner.id,
                    Notification.type == "dependency_ready",
                )
            )
        )
        .scalars()
        .all()
    )
    assert notes == []


async def test_no_notification_while_other_dependency_unmet(
    db_session, client, make_headers
):
    """多前置：只有**其余前置都终态」才发（半就绪不吵人）。"""
    from app.services.request_service import settle_request_status

    owner = await _user(db_session)
    pre1 = await _make_request(db_session, owner, "前置1", status="running")
    pre2 = await _make_request(db_session, owner, "前置2", status="running")
    dependent = await _make_request(db_session, owner, "后续")
    await _edge(db_session, dependent, pre1)
    await _edge(db_session, dependent, pre2)

    db_session.add(
        Run(
            request_id=pre1.id,
            owner_id=owner.id,
            assignee_type="agent",
            assignee_id=str(uuid.uuid4()),
            strategy="direct",
            status="completed",
        )
    )
    await db_session.commit()
    await settle_request_status(db_session, pre1.id)
    await db_session.commit()

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == owner.id,
                    Notification.type == "dependency_ready",
                )
            )
        )
        .scalars()
        .all()
    )
    assert notes == []  # pre2 还没好，不发

    db_session.add(
        Run(
            request_id=pre2.id,
            owner_id=owner.id,
            assignee_type="agent",
            assignee_id=str(uuid.uuid4()),
            strategy="direct",
            status="failed",
        )
    )
    await db_session.commit()
    await settle_request_status(db_session, pre2.id)
    await db_session.commit()

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == owner.id,
                    Notification.type == "dependency_ready",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1  # 全就绪，发一条（对最后收敛的那个前置）


# ---------- FR-010：详情派生字段 + 列表 blocked ----------


async def test_detail_includes_dependencies_and_blocked(
    db_session, client, make_headers
):
    """详情端返回 dependencies（边+标题+状态+terminal）与 blocked 布尔。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    done = await _make_request(db_session, owner, "已完成前置", status="completed")
    running = await _make_request(db_session, owner, "执行中前置", status="running")
    dependent = await _make_request(db_session, owner, "后续")
    await _edge(db_session, dependent, done)
    await _edge(db_session, dependent, running)

    resp = await client.get(f"/api/v1/requests/{dependent.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is True
    by_title = {d["title"]: d for d in body["dependencies"]}
    assert by_title["已完成前置"]["terminal"] is True
    assert by_title["执行中前置"]["terminal"] is False
    assert by_title["执行中前置"]["status"] == "running"


async def test_list_includes_blocked_flag(db_session, client, make_headers):
    """列表端每项带 blocked 布尔（零 N+1：一次 IN 查询）。"""
    owner = await _user(db_session)
    headers = await make_headers(owner)
    blocker = await _make_request(db_session, owner, "阻塞者", status="running")
    blocked_req = await _make_request(db_session, owner, "被挡的")
    free_req = await _make_request(db_session, owner, "自由的")
    await _edge(db_session, blocked_req, blocker)

    resp = await client.get("/api/v1/requests", headers=headers)
    items = {i["id"]: i for i in resp.json()["items"]}
    assert items[blocked_req.id]["blocked"] is True
    assert items[free_req.id]["blocked"] is False
    # 列表端不填边明细（字段留空——共用 response model，null 即「未填充」；
    # 明细只在详情端，避免 N+1）
    assert items[blocked_req.id]["dependencies"] is None
