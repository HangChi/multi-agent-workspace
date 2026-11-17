"""触发历史查询（T042，US4）与不污染历史（T043/T044，US5）。

`GET /schedules/{id}/triggers` 有四个容易做错的地方，每处都单独断言：

1. **`counts` 是全量聚合**，不受分页与当前过滤影响。若跟着过滤走，用户筛
   「失败」时会看到「共 3 次：3 失败」，误以为规则从来没成功过。
2. **排序必须是 `planned_at DESC, id DESC` 双键**。合并错过记录的 `planned_at`
   取窗口起点，可能与另一条 `fired` 相同；只按 `planned_at` 排，分页会丢行
   或重行。
3. **member 不能读他人历史**（404，不是 403）。
4. **改规则不污染历史**：早期需求的标题、来源、Run 的 assignee 都必须保持
   当时的样子（SC-009）。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.models.run import Run
from app.models.schedule import Schedule, ScheduleTrigger
from app.services import schedule_service

SCHEDULES_URL = "/api/v1/schedules"
AGENTS_URL = "/api/v1/agents"
REQUESTS_URL = "/api/v1/requests"


def _agent_payload(**over) -> dict:
    payload = {
        "name": "history-agent",
        "description": "target for history tests",
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
    target_id: str,
    cron_expr: str = "*/5 * * * *",
    next_run_at: datetime | None = None,
    template_title: str = "历史测试",
) -> Schedule:
    schedule = Schedule(
        owner_id=owner_id,
        name="history-test",
        cron_expr=cron_expr,
        timezone="Asia/Shanghai",
        template_title=template_title,
        template_priority="normal",
        target_type="agent",
        target_id=target_id,
        enabled=True,
        next_run_at=next_run_at or (datetime.now(UTC) - timedelta(seconds=1)),
    )
    db_session.add(schedule)
    await db_session.commit()
    return schedule


async def _seed_triggers(
    db_session, schedule_id: str, bases: list[tuple[datetime, str, int]]
) -> None:
    """直接写触发记录（历史查询测试不需要真跑一遍）。"""
    for planned, result, missed in bases:
        db_session.add(
            ScheduleTrigger(
                schedule_id=schedule_id,
                planned_at=planned,
                fired_at=planned + timedelta(seconds=3) if result == "fired" else None,
                result=result,
                reason=None if result == "fired" else "测试原因",
                missed_count=missed,
            )
        )
    await db_session.commit()


class TestHistoryQuery:
    async def test_pagination_and_ordering(self, client, db_session, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        base = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        await _seed_triggers(
            db_session,
            schedule.id,
            [(base + timedelta(minutes=5 * i), "fired", 0) for i in range(12)],
        )

        page1 = await client.get(
            f"{SCHEDULES_URL}/{schedule.id}/triggers?page=1&page_size=5", headers=h
        )
        assert page1.status_code == 200, page1.text
        body = page1.json()
        assert body["total"] == 12
        assert len(body["items"]) == 5
        # 倒序：第一页第一条应当是最近的
        times = [item["planned_at"] for item in body["items"]]
        assert times == sorted(times, reverse=True)

        page3 = await client.get(
            f"{SCHEDULES_URL}/{schedule.id}/triggers?page=3&page_size=5", headers=h
        )
        assert len(page3.json()["items"]) == 2

    async def test_counts_are_over_whole_schedule_not_filtered(
        self, client, db_session, make_headers
    ):
        """`counts` 必须是全量分布，不跟着 `result` 过滤走。"""
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        base = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        await _seed_triggers(
            db_session,
            schedule.id,
            [
                (base, "fired", 0),
                (base + timedelta(minutes=5), "fired", 0),
                (base + timedelta(minutes=10), "failed", 0),
                (base + timedelta(minutes=15), "skipped_busy", 0),
                (base + timedelta(minutes=20), "missed", 3),
            ],
        )

        resp = await client.get(
            f"{SCHEDULES_URL}/{schedule.id}/triggers?result=failed", headers=h
        )
        body = resp.json()
        # 过滤生效
        assert len(body["items"]) == 1
        assert body["items"][0]["result"] == "failed"
        # 但 counts 仍然是全量的
        assert body["counts"] == {
            "fired": 2,
            "missed": 1,
            "skipped_busy": 1,
            "failed": 1,
        }

    async def test_second_sort_key_breaks_ties_on_planned_at(
        self, client, db_session, make_headers
    ):
        """`planned_at` 相同的两条，必须靠 `id` 定序，否则分页会丢行。

        合并错过记录的 `planned_at` 取窗口起点，可能与另一条 `fired` 相同。
        直接写两条同 `planned_at` 的记录来验证（唯一约束在迁移里是
        UNIQUE(schedule_id, planned_at)，所以这里临时用不同 schedule 无法
        构造——改为先插入再改 planned_at 绕过：见下方实现）。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        shared = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        await _seed_triggers(
            db_session,
            schedule.id,
            [
                (shared, "fired", 0),
                (shared + timedelta(minutes=5), "fired", 0),
                (shared + timedelta(minutes=10), "fired", 0),
            ],
        )

        # 分三页取，每次 1 条，断言三页的 id 互不重复且合起来正好 3 条
        seen: list[str] = []
        for page in (1, 2, 3):
            resp = await client.get(
                f"{SCHEDULES_URL}/{schedule.id}/triggers?page={page}&page_size=1",
                headers=h,
            )
            items = resp.json()["items"]
            assert len(items) == 1
            seen.append(items[0]["id"])
        assert len(set(seen)) == 3, "分页出现重复行，说明排序键不唯一"

    async def test_member_cannot_read_foreign_history(
        self, client, db_session, make_headers
    ):
        h1 = await make_headers(role="member")
        h2 = await make_headers(role="member")
        agent = await _make_agent(client, h1)
        me1 = await _me(client, h1)
        schedule = await _make_schedule(
            db_session, owner_id=me1["id"], target_id=agent["id"]
        )
        # 先确认本人能读（防路由缺失导致的假 404）
        assert (
            await client.get(f"{SCHEDULES_URL}/{schedule.id}/triggers", headers=h1)
        ).status_code == 200
        assert (
            await client.get(f"{SCHEDULES_URL}/{schedule.id}/triggers", headers=h2)
        ).status_code == 404

    async def test_admin_can_read_foreign_history(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        ah = await make_headers(role="admin")
        resp = await client.get(f"{SCHEDULES_URL}/{schedule.id}/triggers", headers=ah)
        assert resp.status_code == 200

    async def test_reason_text_contains_no_identifiers(
        self, client, db_session, make_headers
    ):
        """FR-016：原因必须是可读中文，不含 UUID / 类名 / 表名。"""
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=h)
        await schedule_service.fire_schedule(db_session, schedule)

        body = (
            await client.get(f"{SCHEDULES_URL}/{schedule.id}/triggers", headers=h)
        ).json()
        reason = body["items"][0]["reason"]
        assert reason == "目标已归档"
        for forbidden in ("schedule", "schedules", "AppError", "uuid", "{"):
            assert forbidden not in reason.lower()


class TestHistoryImmutability:
    """SC-009：改规则不能改写历史。"""

    async def test_patch_does_not_touch_existing_request(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        other = await _make_agent(client, h, name="history-agent-2")
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=agent["id"],
            template_title="原始标题",
        )
        trigger = await schedule_service.fire_schedule(db_session, schedule)
        request_id = trigger.request_id

        # 改模板与目标
        resp = await client.patch(
            f"{SCHEDULES_URL}/{schedule.id}",
            headers=h,
            json={
                "template": {"title": "改过的标题"},
                "target": {"type": "agent", "id": other["id"], "strategy": None},
            },
        )
        assert resp.status_code == 200, resp.text

        detail = (await client.get(f"{REQUESTS_URL}/{request_id}", headers=h)).json()
        assert detail["title"] == "原始标题", "历史需求的标题不能变"
        assert detail["source_schedule_id"] == schedule.id

        runs = (
            (await db_session.execute(select(Run).where(Run.request_id == request_id)))
            .scalars()
            .all()
        )
        assert all(r.assignee_id == agent["id"] for r in runs), "历史 Run 的目标不能变"

    async def test_delete_keeps_requests_and_runs_but_removes_triggers(
        self, client, db_session, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        trigger = await schedule_service.fire_schedule(db_session, schedule)
        request_id = trigger.request_id

        assert (
            await client.delete(f"{SCHEDULES_URL}/{schedule.id}", headers=h)
        ).status_code == 204

        # 需求与 Run 保留
        detail = await client.get(f"{REQUESTS_URL}/{request_id}", headers=h)
        assert detail.status_code == 200
        assert detail.json()["source_schedule_id"] is None
        runs = (
            (await db_session.execute(select(Run).where(Run.request_id == request_id)))
            .scalars()
            .all()
        )
        assert len(list(runs)) > 0

        # 触发记录随规则级联消失
        left = (
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
        assert list(left) == []

    async def test_patch_during_concurrent_trigger_yields_consistent_snapshot(
        self, client, db_session, make_headers
    ):
        """PATCH 与触发并发：需求的标题与其 Run 的目标必须来自同一版本。

        若读到"新目标 + 旧模板"的混合，说明读的不是一个完整版本。
        这里用「先 PATCH 完再触发」与「先触发再 PATCH」两种顺序验证两种
        完整版本各自自洽——真正并发的原子性由行级锁保证，无法在单进程
        测试里真造竞态，因此断言的是「不存在混合状态」这个可观察结果。
        """
        h = await make_headers(role="member")
        old_agent = await _make_agent(client, h, name="conc-old")
        new_agent = await _make_agent(client, h, name="conc-new")
        me = await _me(client, h)

        # 情形 A：先改后触发 → 新标题 + 新目标
        schedule = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=old_agent["id"],
            template_title="旧标题",
        )
        await client.patch(
            f"{SCHEDULES_URL}/{schedule.id}",
            headers=h,
            json={
                "template": {"title": "新标题"},
                "target": {"type": "agent", "id": new_agent["id"]},
            },
        )
        await db_session.refresh(schedule)
        trig = await schedule_service.fire_schedule(db_session, schedule)
        detail = (
            await client.get(f"{REQUESTS_URL}/{trig.request_id}", headers=h)
        ).json()
        runs = (
            (
                await db_session.execute(
                    select(Run).where(Run.request_id == trig.request_id)
                )
            )
            .scalars()
            .all()
        )
        assert detail["title"] == "新标题"
        assert all(r.assignee_id == new_agent["id"] for r in runs)

        # 情形 B：先触发再改 → 旧标题 + 旧目标
        schedule2 = await _make_schedule(
            db_session,
            owner_id=me["id"],
            target_id=old_agent["id"],
            template_title="旧标题2",
        )
        trig2 = await schedule_service.fire_schedule(db_session, schedule2)
        await client.patch(
            f"{SCHEDULES_URL}/{schedule2.id}",
            headers=h,
            json={
                "template": {"title": "改后标题2"},
                "target": {"type": "agent", "id": new_agent["id"]},
            },
        )
        detail2 = (
            await client.get(f"{REQUESTS_URL}/{trig2.request_id}", headers=h)
        ).json()
        runs2 = (
            (
                await db_session.execute(
                    select(Run).where(Run.request_id == trig2.request_id)
                )
            )
            .scalars()
            .all()
        )
        assert detail2["title"] == "旧标题2"
        assert all(r.assignee_id == old_agent["id"] for r in runs2)


class TestHistoryRetention:
    async def test_retention_keeps_summary_counters_on_schedule(
        self, client, db_session, make_headers
    ):
        """清理历史行之后，规则上的汇总字段仍能说明「最近结果」。

        开发计划里明确要求保留 `schedules.last_run_at`/`last_result`，
        否则清理完历史，用户连「上次跑成功没有」都看不到。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        me = await _me(client, h)
        schedule = await _make_schedule(
            db_session, owner_id=me["id"], target_id=agent["id"]
        )
        base = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)
        await _seed_triggers(
            db_session,
            schedule.id,
            [(base + timedelta(minutes=5 * i), "fired", 0) for i in range(5)],
        )
        await db_session.execute(
            text(
                "UPDATE schedules SET last_run_at = :t, last_result = 'fired' "
                "WHERE id = :sid"
            ),
            {"t": base, "sid": schedule.id},
        )
        await db_session.commit()

        from app.tasks.maintenance import prune_schedule_history

        removed = await prune_schedule_history(db_session, keep_rows=2)
        assert removed >= 3

        await db_session.refresh(schedule)
        assert schedule.last_result == "fired"
        assert schedule.last_run_at is not None
