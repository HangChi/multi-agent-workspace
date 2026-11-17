"""判定顺序与错过/跳过/失败/自动停用（T032/T033/T034/T035，US3）。

**本文件的核心是 `TestJudgementOrder`。** 契约 §3 的 7 步顺序不是风格偏好，
顺序写反会产生"看起来正常、实际掩盖了运维事实"的结果。其中最关键的一条：

> 停机 2 小时后恢复，同时"前次需求仍在 running"→ 必须是 `missed`，不能是
> `skipped_busy`。

因为停机时进程被杀，Run 会停在 running——"前次未结束"往往正是停机造成的。
若先判第 4 步，用户看到"上次运行尚未结束"（像是自己的任务太慢），而真相是
"服务停了两小时，错过了 24 次"。这是排障时最误导人的错误信息。

三个实测数字（research R-07 已确认，来自 T007）：
- 5 分钟规则停机 2 小时（01:00 → 03:00）= **25** 次
- 停机 9 分钟（01:00 → 01:09）= **2** 次
- 延迟 45 秒（< 90s 宽限）= **1** 次真正触发，不是错过
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.core.config import settings
from app.models.request import Request
from app.models.schedule import Schedule, ScheduleTrigger
from app.models.user import User
from app.services import quota_service, schedule_service

AGENTS_URL = "/api/v1/agents"


def _agent_payload(**over) -> dict:
    payload = {
        "name": "judge-agent",
        "description": "target for judgement tests",
        "system_prompt": "you are a helpful assistant",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_steps": 10,
        "max_run_seconds": 1800,
        "tools": [],
        "cost_limit_cents": 10000,
    }
    payload.update(over)
    return payload


async def _make_agent(client, headers: dict, **over) -> dict:
    resp = await client.post(AGENTS_URL, headers=headers, json=_agent_payload(**over))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _me(client, headers: dict) -> dict:
    resp = await client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _make_schedule(
    db_session,
    *,
    owner_id: str,
    target_type: str = "agent",
    target_id: str,
    cron_expr: str = "0 9 * * *",
    next_run_at: datetime | None = None,
) -> Schedule:
    schedule = Schedule(
        owner_id=owner_id,
        name="judge-test",
        cron_expr=cron_expr,
        timezone="Asia/Shanghai",
        template_title="judge",
        template_priority="normal",
        target_type=target_type,
        target_id=target_id,
        enabled=True,
        next_run_at=next_run_at or (datetime.now(UTC) - timedelta(seconds=1)),
    )
    db_session.add(schedule)
    await db_session.commit()
    return schedule


async def _triggers(db_session, schedule_id: str) -> list[ScheduleTrigger]:
    rows = (
        (
            await db_session.execute(
                select(ScheduleTrigger)
                .where(ScheduleTrigger.schedule_id == schedule_id)
                .order_by(ScheduleTrigger.planned_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _requests_for(db_session, schedule_id: str) -> list[Request]:
    rows = (
        (
            await db_session.execute(
                select(Request).where(Request.source_schedule_id == schedule_id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


class TestMissedMerging:
    """停机恢复：合并成一条记录，不补跑。"""

    async def test_two_hour_outage_records_25_missed_in_one_row(
        self, client, db_session, make_headers
    ):
        """每 5 分钟规则、01:00 计划、03:00 恢复 → 1 条 missed_count=25。

        这个数字是实测出来的：01:00…03:00 闭区间内共 25 个 5 分钟刻度。
        注意不是 24——03:00 本身那一班也过期了（lag 恰好 2 小时 > 90s 宽限）。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)

        planned = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        now = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr="*/5 * * * *",
            next_run_at=planned,
        )

        trigger = await schedule_service.fire_schedule(db_session, schedule, now=now)
        assert trigger is not None
        assert trigger.result == "missed"
        assert trigger.missed_count == 25
        assert trigger.fired_at is None
        assert trigger.window_start == planned
        assert trigger.window_end == now
        assert trigger.reason == "服务未运行，错过 25 次"

        # 不补跑：零个新需求
        assert await _requests_for(db_session, schedule.id) == []
        # 只有一条记录（合并）
        assert len(await _triggers(db_session, schedule.id)) == 1

        await db_session.refresh(schedule)
        # next_run_at 推进到 03:05
        assert schedule.next_run_at == datetime(2026, 11, 17, 3, 5, tzinfo=UTC)

    async def test_nine_minute_outage_records_2_missed(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        planned = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        now = datetime(2026, 11, 17, 1, 9, tzinfo=UTC)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr="*/5 * * * *",
            next_run_at=planned,
        )

        trigger = await schedule_service.fire_schedule(db_session, schedule, now=now)
        assert trigger.result == "missed"
        # 跨过 1:00 与 1:05 两个刻度
        assert trigger.missed_count == 2

    async def test_delay_within_grace_fires_instead_of_missing(
        self, client, db_session, make_headers
    ):
        """延迟 45 秒（< 90s 宽限）应当真正执行，不记错过。

        宽限期存在的意义就是这个：一次短暂的调度抖动不该被记成"服务挂了"，
        也不该把这一班任务白白丢掉。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        planned = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        now = planned + timedelta(seconds=45)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr="*/5 * * * *",
            next_run_at=planned,
        )

        trigger = await schedule_service.fire_schedule(db_session, schedule, now=now)
        assert trigger.result == "fired"
        assert trigger.missed_count == 0
        assert len(await _requests_for(db_session, schedule.id)) == 1

    async def test_grace_boundary_is_exclusive(self, client, db_session, make_headers):
        """恰好等于宽限期仍是 fired，超过 1 秒就是 missed。"""
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        planned = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        grace = settings.schedule_missed_grace_seconds

        exact = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr="*/5 * * * *",
            next_run_at=planned,
        )
        trig = await schedule_service.fire_schedule(
            db_session, exact, now=planned + timedelta(seconds=grace)
        )
        assert trig.result == "fired"

        over = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr="*/5 * * * *",
            next_run_at=planned,
        )
        trig2 = await schedule_service.fire_schedule(
            db_session, over, now=planned + timedelta(seconds=grace + 1)
        )
        assert trig2.result == "missed"


class TestJudgementOrder:
    """契约 §3 的顺序。最关键的一条在最后。"""

    async def test_disabled_rule_writes_no_record_at_all(
        self, client, db_session, make_headers
    ):
        """停用状态下到点：不写任何记录（不是 skipped_busy，也不是 missed）。

        这条容易被实现成"先占位再判 enabled"，那样每次扫描都会往历史里
        堆一条无意义的记录，用户看到一堆"被跳过"却不知道原因。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        schedule.enabled = False
        await db_session.commit()

        assert await schedule_service.fire_schedule(db_session, schedule) is None
        assert await _triggers(db_session, schedule.id) == []

    async def test_null_next_run_is_a_noop(self, client, db_session, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        # CHECK 约束不允许启用态 next_run_at 为 NULL，所以先停用再置空
        schedule.enabled = False
        schedule.next_run_at = None
        await db_session.commit()

        assert await schedule_service.fire_schedule(db_session, schedule) is None
        assert await _triggers(db_session, schedule.id) == []

    async def test_busy_previous_request_yields_skipped_busy(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        cron = "*/5 * * * *"
        planned = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr=cron,
            next_run_at=planned,
        )
        # 先正常跑一次，留下一个 running 的需求
        first = await schedule_service.fire_schedule(
            db_session, schedule, now=planned + timedelta(seconds=5)
        )
        assert first.result == "fired"

        # 下一次到点，前次仍在 running
        await db_session.refresh(schedule)
        second_planned = schedule.next_run_at
        second = await schedule_service.fire_schedule(
            db_session, schedule, now=second_planned + timedelta(seconds=5)
        )
        assert second.result == "skipped_busy"
        assert second.reason == "上次运行尚未结束"
        # 没有新需求
        assert len(await _requests_for(db_session, schedule.id)) == 1

    async def test_completed_previous_request_fires_normally(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        planned = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr="*/5 * * * *",
            next_run_at=planned,
        )
        first = await schedule_service.fire_schedule(
            db_session, schedule, now=planned + timedelta(seconds=5)
        )
        # 把前次需求推到终态
        req = await db_session.get(Request, first.request_id)
        req.status = "completed"
        await db_session.commit()

        await db_session.refresh(schedule)
        second = await schedule_service.fire_schedule(
            db_session, schedule, now=schedule.next_run_at + timedelta(seconds=5)
        )
        assert second.result == "fired"

    async def test_outage_with_busy_previous_request_forms_missed_not_busy(
        self, client, db_session, make_headers
    ):
        """★ 决定顺序的一条：停机 2 小时 + 前次仍在 running → `missed`。

        若实现把"前次未结束"判在"错过"之前（即交换第 3、4 步），这条会变成
        `skipped_busy`，用户看到"上次运行尚未结束"，而真相是服务停了两小时、
        错过了 24 次。这是本特性最误导人的失败模式。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        planned = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr="*/5 * * * *",
            next_run_at=planned,
        )
        # 01:00:05 正常跑一次 → 需求 running
        first = await schedule_service.fire_schedule(
            db_session, schedule, now=planned + timedelta(seconds=5)
        )
        assert first.result == "fired"
        req = await db_session.get(Request, first.request_id)
        assert req.status == "running"

        # 把 next_run_at 拨回 01:05（上一次之后的下一班），然后 03:00 才恢复
        await db_session.execute(
            text("UPDATE schedules SET next_run_at = :p WHERE id = :sid"),
            {"p": datetime(2026, 11, 17, 1, 5, tzinfo=UTC), "sid": schedule.id},
        )
        await db_session.commit()
        db_session.expire_all()
        await db_session.refresh(schedule)

        trigger = await schedule_service.fire_schedule(
            db_session, schedule, now=datetime(2026, 11, 17, 3, 0, tzinfo=UTC)
        )
        assert trigger.result == "missed", (
            "停机 + 前次 running 必须记 missed；记成 skipped_busy 说明"
            "第 3 步（错过）被判在了第 4 步（前次未结束）之后"
        )
        assert trigger.missed_count > 0
        # 前次那个 running 需求不受影响（调度不碰别人的 Run）
        await db_session.refresh(req)
        assert req.status == "running"


class TestFailurePaths:
    async def test_archived_target_reason(self, client, db_session, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=h)

        trigger = await schedule_service.fire_schedule(db_session, schedule)
        assert trigger.result == "failed"
        assert trigger.reason == "目标已归档"
        assert await _requests_for(db_session, schedule.id) == []

    async def test_deleted_target_reason(self, client, db_session, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        assert (
            await client.delete(f"{AGENTS_URL}/{agent['id']}", headers=h)
        ).status_code == 204

        trigger = await schedule_service.fire_schedule(db_session, schedule)
        assert trigger.result == "failed"
        assert trigger.reason == "目标已删除"

    async def test_foreign_target_does_not_leak_existence(
        self, client, db_session, make_headers
    ):
        """目标属于他人 → `目标不可用`，不泄露"存在但无权"。

        如果这里写成"目标不属于你"，就等于告诉用户这个 UUID 真实存在。
        """
        h1 = await make_headers(role="member")
        h2 = await make_headers(role="member")
        foreign = await _make_agent(client, h1)
        me2 = await _me(client, h2)
        # 直接用模型建规则（绕过创建时的归属校验，模拟目标事后被转交）
        schedule = await _make_schedule(
            db_session, owner_id=me2["id"], target_id=foreign["id"]
        )

        trigger = await schedule_service.fire_schedule(db_session, schedule)
        assert trigger.result == "failed"
        assert trigger.reason == "目标不可用"
        # 措辞里不能出现"不存在"之外的权限线索
        assert "无权" not in (trigger.reason or "")

    async def test_disabled_owner_reason(self, client, db_session, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        owner = await db_session.get(User, me["id"])
        owner.status = "disabled"
        await db_session.commit()

        trigger = await schedule_service.fire_schedule(db_session, schedule)
        assert trigger.result == "failed"
        assert trigger.reason == "创建者已停用"

    async def test_quota_exhausted_does_not_count_as_consecutive_failure(
        self, client, db_session, make_headers, monkeypatch
    ):
        """配额不足记 failed，但**不累加** consecutive_failures。

        配额是"这个月花完了"，不是规则本身有问题。累加会让规则在用户充值后
        仍被自动停用——用户付了钱却发现调度还是死的。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        # 配额是全局常量（不是用户列），用 monkeypatch 压到 0
        monkeypatch.setattr(quota_service, "MONTHLY_QUOTA_CENTS", 0)

        trigger = await schedule_service.fire_schedule(db_session, schedule)
        assert trigger.result == "failed"
        assert trigger.reason == "本月配额已用尽"
        await db_session.refresh(schedule)
        assert schedule.consecutive_failures == 0, (
            "配额不足不该计入连续失败，否则用户充值后规则仍被自动停用"
        )


class TestAutoDisable:
    async def test_fifth_failure_disables_rule(self, client, db_session, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr="*/5 * * * *",
        )
        await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=h)

        threshold = settings.schedule_max_failures
        for _ in range(threshold):
            await db_session.refresh(schedule)
            if not schedule.enabled:
                break
            await schedule_service.fire_schedule(db_session, schedule)

        await db_session.refresh(schedule)
        assert schedule.consecutive_failures == threshold
        assert schedule.enabled is False
        # 停用态必须没有 next_run_at（CHECK 约束要求）
        assert schedule.next_run_at is None
        assert schedule.auto_disabled_reason
        assert "自动停用" in schedule.auto_disabled_reason

        # 之后不再产生记录
        before = len(await _triggers(db_session, schedule.id))
        assert await schedule_service.fire_schedule(db_session, schedule) is None
        assert len(await _triggers(db_session, schedule.id)) == before

    async def test_success_resets_failure_counter(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            cron_expr="*/5 * * * *",
        )
        # 先制造两次失败
        await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=h)
        for _ in range(2):
            await db_session.refresh(schedule)
            await schedule_service.fire_schedule(db_session, schedule)
        await db_session.refresh(schedule)
        assert schedule.consecutive_failures == 2

        # 取消归档后恢复正常（无 restore 端点，直接改库）
        await db_session.execute(
            text("UPDATE agents SET status = 'active' WHERE id = :aid"),
            {"aid": agent["id"]},
        )
        await db_session.commit()
        db_session.expire_all()
        await db_session.refresh(schedule)
        trig = await schedule_service.fire_schedule(db_session, schedule)
        assert trig.result == "fired"
        await db_session.refresh(schedule)
        assert schedule.consecutive_failures == 0

    async def test_manual_disable_clears_auto_disable_reason(
        self, client, db_session, make_headers
    ):
        """用户主动停用时清掉自动停用原因。

        那段文字描述的是"上次为什么被系统停"，用户已经主动关了，
        留着会让界面显示一段过期的解释。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        resp = await client.post(
            "/api/v1/schedules",
            headers=h,
            json={
                "name": "n",
                "cron_expr": "0 9 * * *",
                "template": {"title": "t"},
                "target": {"type": "agent", "id": agent["id"]},
            },
        )
        assert resp.status_code == 201, resp.text
        sid = resp.json()["id"]

        # 手动写一个自动停用原因，再手动停用
        await db_session.execute(
            text(
                "UPDATE schedules SET auto_disabled_reason = '旧原因' WHERE id = :sid"
            ),
            {"sid": sid},
        )
        await db_session.commit()
        patch = await client.patch(
            f"/api/v1/schedules/{sid}", headers=h, json={"enabled": False}
        )
        assert patch.status_code == 200
        assert patch.json()["auto_disabled_reason"] is None
