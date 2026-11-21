"""调度触发时「依赖门禁建单不分配」分支的提交完整性（D4）。

背景：`fire_schedule` 在这个分支上曾经「先 commit 再改触发记录」——
`_finish_trigger` 只改内存不提交，于是 next_run_at 的推进落在外层
`run_due_schedules` 的那次 commit 上。一旦外层 `write_audit` 失败并
rollback，规则就永久卡在到期态（占位行已存在 → 以后每轮都
`schedule_trigger_duplicate` → 再也不触发）。

关于可达性：调度建的需求是**全新**需求，创建时不可能已有依赖边，
正常路径下 `assert_not_blocked` 必然放行——这个分支目前是防御性的。
但提交顺序是错的（先落需求、后改触发记录，中间态可见），一旦将来
把「模板带依赖」接进调度就是真事故。所以这里用注入异常的方式直接
锁住该分支的提交语义，不依赖它当前是否可达。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.request import Request
from app.models.schedule import Schedule, ScheduleTrigger
from app.services import assignment_service, schedule_service
from app.services.dependency_service import DependencyBlockedError

AGENTS_URL = "/api/v1/agents"


def _agent_payload(**over) -> dict:
    payload = {
        "name": "gate-agent",
        "description": "target for dependency-gate tests",
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


async def _setup_schedule(client, db_session, make_headers) -> tuple[Schedule, datetime]:
    h = await make_headers(role="member")
    resp = await client.post(AGENTS_URL, headers=h, json=_agent_payload())
    assert resp.status_code == 201, resp.text
    agent = resp.json()

    me = (await client.get("/api/v1/auth/me", headers=h)).json()

    planned = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    schedule = Schedule(
        owner_id=me["id"],
        name="gate-test",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        template_title="gate",
        template_priority="normal",
        target_type="agent",
        target_id=agent["id"],
        enabled=True,
        next_run_at=planned,
    )
    db_session.add(schedule)
    await db_session.commit()
    return schedule, planned


class TestDependencyGateCommit:
    """门禁分支必须把「需求 + 触发记录 + next_run_at」在同一事务里落地。"""

    async def test_gate_blocked_advances_next_run_at(
        self, client, db_session, make_headers, monkeypatch
    ):
        schedule, planned = await _setup_schedule(client, db_session, make_headers)
        now = planned + timedelta(seconds=30)  # 在宽限内，不会被判 missed

        async def _blocked(*_args, **_kwargs):
            raise DependencyBlockedError("有 1 个前置需求未完成：x（running）")

        monkeypatch.setattr(assignment_service, "assign", _blocked)

        trigger = await schedule_service.fire_schedule(db_session, schedule, now=now)
        assert trigger is not None

        # 触发记录记 fired（不是 failed：门禁是正常状态，不该累积失败计数）
        assert trigger.result == "fired"
        assert trigger.reason == "前置未完成，未自动分配"
        assert trigger.request_id is not None
        assert trigger.run_id is None

        # 需求确实建了
        requests = (
            (
                await db_session.execute(
                    select(Request).where(Request.source_schedule_id == schedule.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(requests) == 1
        assert requests[0].id == trigger.request_id

        # **核心断言**：next_run_at 必须已推进到过去时刻之后。
        # 修复前这里会是旧值（推进只改了内存、没提交），
        # 导致下一轮扫描仍判定到期 → 撞占位行 → 永久不再触发。
        #
        # 不锁具体时刻：cron 按 Asia/Shanghai 解释，09:00 UTC == 上海 17:00，
        # 下一次是上海 09:00（=次日 01:00 UTC）。只断言「落在 now 之后且
        # 不等于原计划时刻」——这才是修复要保证的性质。
        await db_session.refresh(schedule)
        assert schedule.next_run_at is not None
        assert schedule.next_run_at > now
        assert schedule.next_run_at != planned

    async def test_gate_blocked_does_not_count_as_failure(
        self, client, db_session, make_headers, monkeypatch
    ):
        """门禁不是失败：连续失败计数保持 0，规则不会被自动停用。"""
        schedule, planned = await _setup_schedule(client, db_session, make_headers)
        schedule.consecutive_failures = 4  # 再失败一次就会被停用
        await db_session.commit()
        now = planned + timedelta(seconds=30)

        async def _blocked(*_args, **_kwargs):
            raise DependencyBlockedError("有 1 个前置需求未完成：x（running）")

        monkeypatch.setattr(assignment_service, "assign", _blocked)

        await schedule_service.fire_schedule(db_session, schedule, now=now)
        await db_session.refresh(schedule)

        assert schedule.consecutive_failures == 4  # 没被 +1
        assert schedule.enabled is True

    async def test_next_scan_does_not_stall_after_gate(
        self, client, db_session, make_headers, monkeypatch
    ):
        """复现原始事故：外层审计失败回滚后，规则不能永久卡死。

        这才是旧代码的真实失效机制。`run_due_schedules` 在 fire_schedule
        返回后写审计（`write_audit(strict=True)`），失败时会 rollback 并抛
        AppError，被外层 `except` 吞掉。

        - 旧顺序（先 commit 再 _finish_trigger）：需求已独立提交，但
          next_run_at 的推进留在未提交的 session 里 → 被 write_audit 的
          rollback 一并丢掉。下一轮扫描仍判定到期，撞占位行唯一键返回
          None → **从此再不触发**（需求建了、规则静默死亡）。
        - 新顺序（先 _finish_trigger 再 commit）：推进已提交，回滚无从破坏。

        本轮用「审计失败」注入，直接走这条路径。
        """
        schedule, planned = await _setup_schedule(client, db_session, make_headers)
        now = planned + timedelta(seconds=30)

        async def _blocked(*_args, **_kwargs):
            raise DependencyBlockedError("有 1 个前置需求未完成：x（running）")

        monkeypatch.setattr(assignment_service, "assign", _blocked)

        # 注入：审计写入失败（模拟磁盘满/DB 故障）→ write_audit 内部 rollback 并抛
        from app.api.errors import AppError
        from app.services import schedule_service as ss_mod

        async def _audit_fails(*_args, **_kwargs):
            await db_session.rollback()
            raise AppError("INTERNAL_ERROR", "审计日志写入失败")

        monkeypatch.setattr(ss_mod, "write_audit", _audit_fails)

        # 第一轮：门禁建单成功，但审计失败 → 外层 rollback
        first = await schedule_service.run_due_schedules(db_session, now=now)
        assert first == 1  # 触发本身算处理过（return 非 None）

        # 关键：next_run_at 必须已经推进（提交不受审计失败影响）
        await db_session.refresh(schedule)
        assert schedule.next_run_at is not None
        assert schedule.next_run_at > now

        # 第二轮（同一时刻）：不该再被选为到期
        monkeypatch.setattr(ss_mod, "write_audit", ss_mod.write_audit)
        second = await schedule_service.run_due_schedules(db_session, now=now)
        assert second == 0

        triggers = (
            (
                await db_session.execute(
                    select(ScheduleTrigger).where(ScheduleTrigger.schedule_id == schedule.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(triggers) == 1  # 没有新增占位行，也没卡在到期态
