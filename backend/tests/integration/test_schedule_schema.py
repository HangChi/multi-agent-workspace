"""调度表结构与约束测试（T008）。

这些断言在应用层之上再验一遍数据库约束，因为它们是**唯一**的最终防线：

- 唯一约束承载幂等（同一计划时刻只建一张单）。应用层若因任何原因重试，
  这里会拦住，而不是变成两张单。
- ON DELETE 语义决定「删规则会不会连带删数据」。写错方向就是静默的数据丢失。

若只在这些约束的 ORM 层做测试，一次迁移写漏就会在线上才暴露 ——
所以这里直接对真实 PostgreSQL 断言。
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request import Request
from app.models.schedule import Schedule, ScheduleTrigger
from app.models.user import User


async def _user(db: AsyncSession, email: str = "sched@test.local") -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash="x",
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    return user


async def _schedule(db: AsyncSession, owner: User, **overrides) -> Schedule:
    from datetime import UTC, datetime

    data = {
        "owner_id": owner.id,
        "name": "每日晨报",
        "cron_expr": "0 9 * * 1-5",
        "timezone": "Asia/Shanghai",
        "template_title": "每日进度汇总",
        "target_type": "agent",
        "target_id": str(uuid.uuid4()),
        "enabled": True,
        "next_run_at": datetime(2026, 11, 18, 1, 0, tzinfo=UTC),
    }
    data.update(overrides)
    schedule = Schedule(**data)
    db.add(schedule)
    await db.flush()
    return schedule


class TestScheduleConstraints:
    async def test_enabled_rule_without_next_run_is_rejected(self, db_session):
        """启用但没有下次触发时间 = 永远不会被扫描命中，必须挡在数据库层。

        没有这条约束时，这类规则在界面上看起来完全正常（"已启用"），
        实际永远不执行 —— 用户不会收到任何错误。
        """
        user = await _user(db_session)
        with pytest.raises(IntegrityError):
            await _schedule(db_session, user, enabled=True, next_run_at=None)
        await db_session.rollback()

    async def test_disabled_rule_may_have_no_next_run(self, db_session):
        """停用态允许 next_run_at 为空 —— 这正是停用时的正常状态。"""
        user = await _user(db_session)
        schedule = await _schedule(db_session, user, enabled=False, next_run_at=None)
        assert schedule.next_run_at is None

    async def test_blank_name_is_rejected(self, db_session):
        user = await _user(db_session)
        with pytest.raises(IntegrityError):
            await _schedule(db_session, user, name="")
        await db_session.rollback()

    async def test_blank_cron_is_rejected(self, db_session):
        user = await _user(db_session)
        with pytest.raises(IntegrityError):
            await _schedule(db_session, user, cron_expr="")
        await db_session.rollback()

    async def test_negative_failure_count_is_rejected(self, db_session):
        user = await _user(db_session)
        with pytest.raises(IntegrityError):
            await _schedule(db_session, user, consecutive_failures=-1)
        await db_session.rollback()


class TestTriggerIdempotency:
    async def test_same_planned_instant_cannot_be_recorded_twice(self, db_session):
        """幂等基石：同一 (schedule, planned_at) 只能有一条。

        这是「同一计划时刻最多建一张单」（FR-012）的最终保证，
        与实现是否重试、是否有多个执行者无关。
        """
        from datetime import UTC, datetime

        user = await _user(db_session)
        schedule = await _schedule(db_session, user)
        planned = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)

        db_session.add(
            ScheduleTrigger(schedule_id=schedule.id, planned_at=planned, result="fired")
        )
        await db_session.flush()

        db_session.add(
            ScheduleTrigger(schedule_id=schedule.id, planned_at=planned, result="fired")
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

    async def test_on_conflict_do_nothing_is_silent(self, db_session):
        """实现会用 ON CONFLICT DO NOTHING 占位；它必须安静返回而不是抛错。

        若这里抛错，"重复投递"会变成每次扫描都报异常，掩盖真正的问题。
        """
        from datetime import UTC, datetime

        user = await _user(db_session)
        schedule = await _schedule(db_session, user)
        planned = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)

        stmt = text(
            """
            INSERT INTO schedule_triggers
                (id, schedule_id, planned_at, result, missed_count, created_at)
            VALUES
                (gen_random_uuid(), :sid, :planned, 'fired', 0, now())
            ON CONFLICT (schedule_id, planned_at) DO NOTHING
            """
        )
        params = {"sid": schedule.id, "planned": planned}

        first = await db_session.execute(stmt, params)
        assert first.rowcount == 1
        second = await db_session.execute(stmt, params)
        assert second.rowcount == 0

        count = await db_session.scalar(
            text("SELECT count(*) FROM schedule_triggers WHERE schedule_id = :sid"),
            {"sid": schedule.id},
        )
        assert count == 1

    async def test_different_instants_are_allowed(self, db_session):
        """唯一键是 (schedule_id, planned_at)，不是只按 schedule，别过度约束。"""
        from datetime import UTC, datetime, timedelta

        user = await _user(db_session)
        schedule = await _schedule(db_session, user)
        base = datetime(2026, 11, 17, 1, 0, tzinfo=UTC)

        for offset in range(3):
            db_session.add(
                ScheduleTrigger(
                    schedule_id=schedule.id,
                    planned_at=base + timedelta(minutes=5 * offset),
                    result="fired",
                )
            )
        await db_session.flush()
        assert (
            await db_session.scalar(
                text("SELECT count(*) FROM schedule_triggers WHERE schedule_id = :sid"),
                {"sid": schedule.id},
            )
            == 3
        )


class TestDeleteSemantics:
    async def test_deleting_schedule_keeps_its_requests(self, db_session):
        """删规则绝不能删掉它已产生的需求（进而连带 Run/Step/制品）。

        规则是"以后怎么触发"，需求是"已经发生过的事"。
        """
        user = await _user(db_session)
        schedule = await _schedule(db_session, user)
        request = Request(
            owner_id=user.id,
            title="由调度产生",
            priority="normal",
            status="created",
            attachments=[],
            source_schedule_id=schedule.id,
        )
        db_session.add(request)
        await db_session.flush()
        request_id = request.id

        await db_session.execute(
            text("DELETE FROM schedules WHERE id = :sid"), {"sid": schedule.id}
        )
        await db_session.flush()

        survivor = await db_session.get(Request, request_id)
        assert survivor is not None, "删规则后需求必须仍然存在"
        # 必须 refresh：原生 DELETE 绕过 ORM，会话身份映射里的 Request 还缓存着
        # 删除前的 source_schedule_id。不 refresh 就断言，测的是缓存不是数据库。
        await db_session.refresh(survivor)
        # 外键应被置空，而不是指向一个已删除的规则
        assert survivor.source_schedule_id is None

    async def test_deleting_schedule_cascades_trigger_history(self, db_session):
        user = await _user(db_session)
        schedule = await _schedule(db_session, user)
        db_session.add(
            ScheduleTrigger(
                schedule_id=schedule.id, planned_at=schedule.next_run_at, result="fired"
            )
        )
        await db_session.flush()

        await db_session.execute(
            text("DELETE FROM schedules WHERE id = :sid"), {"sid": schedule.id}
        )
        await db_session.flush()

        assert (
            await db_session.scalar(text("SELECT count(*) FROM schedule_triggers")) == 0
        )

    async def test_manual_requests_have_null_source(self, db_session):
        """手工创建的需求该列为 NULL —— 新增字段必须可空，否则破坏既有创建路径。"""
        user = await _user(db_session)
        request = Request(
            owner_id=user.id,
            title="手工创建",
            priority="normal",
            status="created",
            attachments=[],
        )
        db_session.add(request)
        await db_session.flush()
        assert request.source_schedule_id is None


class TestIndexShape:
    async def test_due_index_is_partial_on_enabled(self, db_session):
        """扫描只用部分索引扫启用中的规则；索引退化会让扫描变全表扫。"""
        definition = await db_session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_schedules_due'"
            )
        )
        assert definition is not None
        assert "WHERE enabled" in definition

    async def test_unique_constraint_exists_in_catalog(self, db_session):
        exists = await db_session.scalar(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conname = 'uq_schedule_triggers_planned' AND contype = 'u'"
            )
        )
        assert exists == 1

    async def test_enum_values_are_exactly_the_four_results(self, db_session):
        """四种结果与 FR-016 一一对应；多一个或少一个都会让契约对不上。"""
        values = (
            (
                await db_session.execute(
                    text(
                        "SELECT enumlabel FROM pg_enum e "
                        "JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = 'schedule_trigger_result' "
                        "ORDER BY e.enumsortorder"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert list(values) == ["fired", "missed", "skipped_busy", "failed"]
