"""调度触发：`fired` 路径与 Run 结构等价性（T021/T022/T023/T024，US2）。

**本文件最重要的一组是 `TestStructuralEquivalence`。** 调度的建单+分配必须
与手工分配产出**逐字段相同**的 Run 行。如果调度路径自己实现一遍建单逻辑，
它会慢慢漂移——某天手工路径加了 `tool_grants_snapshot` 冻结、加了外部工具
授权快照，调度路径不会跟着改，而表面上"任务跑起来了"，没人会发现它跑在
一套没有授权边界的 Run 上。

因此这里不是"断言 Run 大致对"，而是对同一个目标先手工分配一次、再让调度
触发一次，把两批 Run 的每个字段拉平比对。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.core.config import settings
from app.models.request import Request
from app.models.run import Run
from app.models.schedule import Schedule, ScheduleTrigger
from app.models.user import User
from app.services import schedule_service

SCHEDULES_URL = "/api/v1/schedules"
AGENTS_URL = "/api/v1/agents"
REQUESTS_URL = "/api/v1/requests"


def _agent_payload(**over) -> dict:
    payload = {
        "name": "dispatch-agent",
        "description": "target for dispatch tests",
        "system_prompt": "you are a helpful assistant",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_steps": 10,
        "max_run_seconds": 1800,
        "tools": ["web_search"],
        "cost_limit_cents": 10000,
    }
    payload.update(over)
    return payload


async def _make_agent(client, headers: dict, **over) -> dict:
    resp = await client.post(AGENTS_URL, headers=headers, json=_agent_payload(**over))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_group(
    client,
    headers: dict,
    *,
    members: list[str] | None = None,
    strategy: str = "fan_out",
) -> dict:
    resp = await client.post(
        "/api/v1/groups",
        headers=headers,
        json={"name": "dispatch-group", "routing_strategy": strategy},
    )
    assert resp.status_code == 201, resp.text
    group = resp.json()
    for index, agent_id in enumerate(members or [], start=1):
        add = await client.post(
            f"/api/v1/groups/{group['id']}/members",
            headers=headers,
            json={"agent_id": agent_id, "position": index},
        )
        assert add.status_code in (200, 201), add.text
    return group


async def _make_schedule(
    db_session,
    *,
    owner_id: str,
    target_type: str,
    target_id: str,
    target_strategy: str | None = None,
    cron_expr: str = "0 9 * * *",
) -> Schedule:
    """直接用模型建规则：触发测试不关心 CRUD 的校验细节。"""
    schedule = Schedule(
        owner_id=owner_id,
        name="dispatch-test",
        cron_expr=cron_expr,
        timezone="Asia/Shanghai",
        template_title="由调度产生的需求",
        template_description="desc",
        template_priority="normal",
        target_type=target_type,
        target_id=target_id,
        target_strategy=target_strategy,
        enabled=True,
        next_run_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(schedule)
    await db_session.commit()
    return schedule


async def _runs_for(db_session, request_id: str) -> list[Run]:
    """取一批 Run，并**归一化成可稳定比对的顺序**。

    不能按 `id` 排序：id 是随机 UUID，两次分配的字典序不同。而 fan_out 的
    兄弟 Run `sequence` 都是 0，`(assignee_type, sequence)` 也不足以定序 ——
    按 id 排会让等价性断言以约 1/3 的概率假失败（实测 5 次里挂 2 次）。
    这里改成按人类可读的业务键排序：父 Run 在前，子 Run 按 assignee_id。
    """
    rows = (
        (await db_session.execute(select(Run).where(Run.request_id == request_id)))
        .scalars()
        .all()
    )
    return sorted(
        rows,
        key=lambda r: (
            r.parent_run_id is not None,  # 父 Run 排前面
            str(r.assignee_id),
            r.sequence,
        ),
    )


def _run_signature(run: Run) -> dict:
    """把 Run 拉平成一个可逐字段比对的签名。

    刻意包含 `tool_grants_snapshot` 的**存在性**而不是内容：内容取决于
    当时的工具目录快照，两次分配之间可能变；但"有没有冻结"必须一致——
    没有冻结的 Run 在执行时才读授权，是权限边界上的漏洞。
    """
    return {
        "assignee_type": run.assignee_type,
        "assignee_id": run.assignee_id,
        "strategy": run.strategy,
        "sequence": run.sequence,
        "has_parent": run.parent_run_id is not None,
        "status": run.status,
        "owner_id": run.owner_id,
        "grants_frozen": run.tool_grants_snapshot is not None,
    }


class TestFiredPath:
    async def test_agent_target_creates_request_and_run(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        user = await db_session.get(User, (await _me(client, h))["id"])
        schedule = await _make_schedule(
            db_session, owner_id=user.id, target_type="agent", target_id=agent["id"]
        )

        trigger = await schedule_service.fire_schedule(db_session, schedule)

        assert trigger is not None
        assert trigger.result == "fired"
        assert trigger.request_id
        request = await db_session.get(Request, trigger.request_id)
        assert request.status == "running"
        assert request.source_schedule_id == schedule.id
        assert request.title == schedule.template_title

        runs = await _runs_for(db_session, request.id)
        assert len(runs) == 1
        assert runs[0].assignee_type == "agent"
        assert runs[0].assignee_id == agent["id"]
        # 未指定策略时按 direct（与手工分配一致）
        assert runs[0].strategy == "direct"

    async def test_group_fan_out_creates_parent_and_children(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        a1 = await _make_agent(client, h, name="fan-1")
        a2 = await _make_agent(client, h, name="fan-2")
        group = await _make_group(
            client, h, members=[a1["id"], a2["id"]], strategy="fan_out"
        )
        user = await db_session.get(User, (await _me(client, h))["id"])

        schedule = await _make_schedule(
            db_session,
            owner_id=user.id,
            target_type="group",
            target_id=group["id"],
            target_strategy="fan_out",
        )
        trigger = await schedule_service.fire_schedule(db_session, schedule)
        assert trigger.result == "fired"

        runs = await _runs_for(db_session, trigger.request_id)
        parents = [r for r in runs if r.parent_run_id is None]
        children = [r for r in runs if r.parent_run_id is not None]
        assert len(parents) == 1
        assert len(children) == 2
        assert {c.assignee_id for c in children} == {a1["id"], a2["id"]}
        assert all(c.parent_run_id == parents[0].id for c in children)

    async def test_group_pipeline_only_first_stage_enqueued(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        a1 = await _make_agent(client, h, name="pipe-1")
        a2 = await _make_agent(client, h, name="pipe-2")
        group = await _make_group(
            client, h, members=[a1["id"], a2["id"]], strategy="pipeline"
        )
        user = await db_session.get(User, (await _me(client, h))["id"])
        schedule = await _make_schedule(
            db_session,
            owner_id=user.id,
            target_type="group",
            target_id=group["id"],
            target_strategy="pipeline",
        )
        trigger = await schedule_service.fire_schedule(db_session, schedule)
        assert trigger.result == "fired"

        runs = await _runs_for(db_session, trigger.request_id)
        # 父 Run + N 个子 Run，sequence 1..N
        children = [r for r in runs if r.parent_run_id is not None]
        assert sorted(c.sequence for c in children) == [1, 2]

    async def test_group_router_creates_single_parent(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        a1 = await _make_agent(client, h, name="router-1")
        group = await _make_group(client, h, members=[a1["id"]], strategy="router")
        user = await db_session.get(User, (await _me(client, h))["id"])
        schedule = await _make_schedule(
            db_session,
            owner_id=user.id,
            target_type="group",
            target_id=group["id"],
            target_strategy="router",
        )
        trigger = await schedule_service.fire_schedule(db_session, schedule)
        assert trigger.result == "fired"

        runs = await _runs_for(db_session, trigger.request_id)
        assert len(runs) == 1
        assert runs[0].assignee_type == "group"
        assert runs[0].strategy == "router"


async def _me(client, headers: dict) -> dict:
    resp = await client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestStructuralEquivalence:
    """SC-003：调度产出的 Run 必须与手工分配逐字段一致。"""

    async def test_agent_runs_match_manual_assignment(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)

        # 手工路径
        req = await client.post(
            REQUESTS_URL, headers=h, json={"title": "手工", "priority": "normal"}
        )
        manual_req = req.json()
        assert (
            await client.post(
                f"{REQUESTS_URL}/{manual_req['id']}/assign",
                headers=h,
                json={"assignee_type": "agent", "assignee_id": agent["id"]},
            )
        ).status_code == 202
        await db_session.commit()
        manual_runs = await _runs_for(db_session, manual_req["id"])

        # 调度路径
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_type="agent", target_id=agent["id"]
        )
        trigger = await schedule_service.fire_schedule(db_session, schedule)
        sched_runs = await _runs_for(db_session, trigger.request_id)

        assert len(manual_runs) == len(sched_runs) == 1
        assert _run_signature(manual_runs[0]) == _run_signature(sched_runs[0])

    async def test_group_fan_out_runs_match_manual_assignment(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        a1 = await _make_agent(client, h, name="eq-1")
        a2 = await _make_agent(client, h, name="eq-2")
        group = await _make_group(client, h, members=[a1["id"], a2["id"]])
        me = await _me(client, h)

        req = await client.post(
            REQUESTS_URL, headers=h, json={"title": "手工", "priority": "normal"}
        )
        manual_req = req.json()
        assert (
            await client.post(
                f"{REQUESTS_URL}/{manual_req['id']}/assign",
                headers=h,
                json={
                    "assignee_type": "group",
                    "assignee_id": group["id"],
                    "strategy": "fan_out",
                },
            )
        ).status_code == 202
        await db_session.commit()
        manual_runs = await _runs_for(db_session, manual_req["id"])

        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_type="group",
            target_id=group["id"],
            target_strategy="fan_out",
        )
        trigger = await schedule_service.fire_schedule(db_session, schedule)
        sched_runs = await _runs_for(db_session, trigger.request_id)

        assert len(manual_runs) == len(sched_runs)
        assert [_run_signature(r) for r in manual_runs] == [
            _run_signature(r) for r in sched_runs
        ]
        # 而且父 Run 必须都冻结了授权快照（orchestration-only 父不该带工具）
        for runs in (manual_runs, sched_runs):
            parent = next(r for r in runs if r.parent_run_id is None)
            assert parent.tool_grants_snapshot == []


class TestAtomicity:
    """FR-009：assign 失败时不能留下半成品需求。"""

    async def test_archived_target_leaves_no_request_and_one_failed_trigger(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_type="agent", target_id=agent["id"]
        )
        before_next = schedule.next_run_at

        # 目标归档，分配必然失败
        assert (
            await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=h)
        ).status_code in (200, 204)

        trigger = await schedule_service.fire_schedule(db_session, schedule)

        assert trigger is not None
        assert trigger.result == "failed"
        assert trigger.request_id is None

        # 没有任何需求残留
        left = (
            (
                await db_session.execute(
                    select(Request).where(Request.source_schedule_id == schedule.id)
                )
            )
            .scalars()
            .all()
        )
        assert list(left) == []

        await db_session.refresh(schedule)
        assert schedule.next_run_at > before_next
        assert schedule.consecutive_failures == 1


class TestIdempotency:
    async def test_same_planned_instant_cannot_produce_two_requests(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_type="agent", target_id=agent["id"]
        )
        planned = schedule.next_run_at

        first = await schedule_service.fire_schedule(db_session, schedule)
        assert first.result == "fired"

        # 人为把 next_run_at 拨回同一计划时刻，模拟"推进失败后重扫"
        await db_session.execute(
            text("UPDATE schedules SET next_run_at = :p WHERE id = :sid"),
            {"p": planned, "sid": schedule.id},
        )
        await db_session.commit()
        await db_session.refresh(schedule)

        second = await schedule_service.fire_schedule(db_session, schedule)
        assert second is None, "同一 planned_at 已处理过，不应再产出记录"

        rows = (
            (
                await db_session.execute(
                    select(ScheduleTrigger).where(
                        ScheduleTrigger.schedule_id == schedule.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(list(rows)) == 1
        reqs = (
            (
                await db_session.execute(
                    select(Request).where(Request.source_schedule_id == schedule.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(list(reqs)) == 1


class TestRequestSourceExposure:
    """T024：需求详情要能追溯到来源规则，且手工需求的两个字段为 null。"""

    async def test_detail_exposes_source_schedule_name(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_type="agent",
            target_id=agent["id"],
        )
        trigger = await schedule_service.fire_schedule(db_session, schedule)

        resp = await client.get(f"{REQUESTS_URL}/{trigger.request_id}", headers=h)
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_schedule_id"] == schedule.id
        assert body["source_schedule_name"] == schedule.name

    async def test_manual_request_has_null_source(self, client, make_headers):
        """手工需求不受影响（001 契约断言必须继续通过）。"""
        h = await make_headers(role="member")
        resp = await client.post(
            REQUESTS_URL, headers=h, json={"title": "手工", "priority": "normal"}
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["source_schedule_id"] is None
        assert body["source_schedule_name"] is None

    async def test_detail_after_schedule_deleted_keeps_request(
        self, client, db_session, make_headers
    ):
        """删规则后需求仍在，但来源 id 与名称都变 null（不是报错）。"""
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_type="agent", target_id=agent["id"]
        )
        trigger = await schedule_service.fire_schedule(db_session, schedule)
        request_id = trigger.request_id

        resp = await client.delete(f"{SCHEDULES_URL}/{schedule.id}", headers=h)
        assert resp.status_code == 204

        detail = await client.get(f"{REQUESTS_URL}/{request_id}", headers=h)
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["source_schedule_id"] is None
        assert body["source_schedule_name"] is None


class TestRunDueSchedules:
    """扫描入口：批量、隔离、以及 `now` 注入。"""

    async def test_only_due_rules_are_processed(self, client, db_session, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)

        due = await _make_schedule(
            db_session, owner_id=me["id"], target_type="agent", target_id=agent["id"]
        )
        # 未来的规则不该被扫到
        later = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_type="agent",
            target_id=agent["id"],
            cron_expr="0 9 * * *",
        )
        later.next_run_at = datetime.now(UTC) + timedelta(hours=5)
        await db_session.commit()

        # 不能断言 processed == 1：扫描的是整张表，同一测试会话里其它用例
        # 留下的到期规则也会被处理（实测混跑时 processed=9）。只断言本用例
        # 关心的两条：到期的被处理、未来的没被碰。
        processed = await schedule_service.run_due_schedules(db_session)
        assert processed >= 1
        await db_session.refresh(due)
        await db_session.refresh(later)
        assert due.last_result == "fired"
        assert later.last_result is None

    async def test_disabled_rule_is_not_processed(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_type="agent", target_id=agent["id"]
        )
        schedule.enabled = False
        schedule.next_run_at = None
        await db_session.commit()

        # 本条独立断言：停用规则不该被处理。扫描可能处理其它用例的规则，
        # 所以只断言本条规则的状态没被碰。
        await schedule_service.run_due_schedules(db_session)
        await db_session.refresh(schedule)
        assert schedule.last_result is None

    async def test_one_broken_rule_does_not_abort_the_batch(
        self, client, db_session, make_headers
    ):
        """FR-018：单条失败不能拖垮整批。

        用一条 cron 表达式被改坏（直接改库绕过校验）的规则制造异常，
        断言另一条正常规则仍被处理。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)

        broken = await _make_schedule(
            db_session, owner_id=me["id"], target_type="agent", target_id=agent["id"]
        )
        healthy = await _make_schedule(
            db_session, owner_id=me["id"], target_type="agent", target_id=agent["id"]
        )
        # 绕过 schema 直接写一个非法表达式。
        # 必须 expire_all 之后才能生效：原生 UPDATE 绕过了 ORM，会话身份映射里
        # 的 Schedule 对象仍缓存着旧的 cron_expr，扫描时会拿到旧值而不是坏值，
        # 测试就会假通过（实测：不 expire 时这条规则会正常 fired，断言 2==1 失败）。
        await db_session.execute(
            text("UPDATE schedules SET cron_expr = 'broken' WHERE id = :sid"),
            {"sid": broken.id},
        )
        await db_session.commit()
        db_session.expire_all()

        processed = await schedule_service.run_due_schedules(db_session)
        # 坏的那条抛异常被隔离，好的那条正常处理。
        # 不断言精确条数：扫描的是全表，同会话其它用例的到期规则也会被处理。
        assert processed >= 1
        await db_session.refresh(healthy)
        assert healthy.last_result == "fired"

    async def test_scan_disabled_returns_zero_without_side_effects(
        self, client, db_session, make_headers, monkeypatch
    ):
        """关掉扫描后，到期规则保持原状。"""
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_type="agent", target_id=agent["id"]
        )
        monkeypatch.setattr(settings, "schedule_scan_enabled", False)

        assert await schedule_service.run_due_schedules(db_session) == 0
        await db_session.refresh(schedule)
        assert schedule.last_result is None
