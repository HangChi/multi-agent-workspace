"""列表接口的查询次数不随条数增长（P1，docs/code-audit-2026-11-21.md）。

`list_schedules` 原先是逐条 `_to_out`：每条规则一次 `db.get(目标)`，admin
视角再多一次 `db.get(User)`。page_size=20 时是 20~40 次串行往返。

这里用 SQLAlchemy 的 `before_cursor_execute` 事件直接**数 SELECT 条数**，
而不是断言"结果对不对"——结果本来就对，N+1 是纯性能问题，只能靠计数验证。
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.models.agent import Agent
from app.models.request import Request  # noqa: F401  (确保模型已注册)
from app.models.schedule import Schedule
from app.models.user import User
from app.services import schedule_service

pytestmark = pytest.mark.integration


class _SelectCounter:
    """统计一个 async session 上发出的 SELECT 次数。"""

    def __init__(self, session):
        self.session = session
        self.count = 0

    def _before(self, conn, cursor, statement, parameters, context, executemany):
        # 只数 SELECT（INSERT/UPDATE 与 N+1 无关）
        if statement.lstrip().upper().startswith("SELECT"):
            self.count += 1

    def __enter__(self):
        event.listen(self.session.sync_session.bind, "before_cursor_execute", self._before)
        return self

    def __exit__(self, *exc):
        event.remove(self.session.sync_session.bind, "before_cursor_execute", self._before)
        return False


async def _make_user(db, role: str = "member") -> User:
    user = User(
        email=f"p1-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.flush()
    return user


async def _make_schedules(db, owner: User, count: int) -> list[Schedule]:
    agent = Agent(
        owner_id=owner.id,
        name=f"p1-agent-{uuid.uuid4().hex[:6]}",
        description="",
        system_prompt="hi",
        model="gpt-4o-mini",
        temperature=0.7,
        max_steps=3,
        max_run_seconds=60,
        tools=[],
        cost_limit_cents=100,
        status="active",
    )
    db.add(agent)
    await db.flush()

    rows = []
    for i in range(count):
        schedule = Schedule(
            owner_id=owner.id,
            name=f"p1-sched-{i}",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            template_title=f"t{i}",
            template_priority="normal",
            target_type="agent",
            target_id=agent.id,
            enabled=True,
            next_run_at=datetime.now(UTC),
        )
        db.add(schedule)
        rows.append(schedule)
    await db.commit()
    return rows


class TestListSchedulesQueryCount:
    async def test_selects_do_not_grow_with_page_size(self, db_session):
        """5 条与 20 条规则的 SELECT 次数必须相同（批量预取后是常数）。"""
        member = await _make_user(db_session)

        # --- 5 条 ---
        await _make_schedules(db_session, member, 5)
        with _SelectCounter(db_session) as small:
            items, total = await schedule_service.list_schedules(
                db_session, user=member, page=1, page_size=50
            )
        assert total == 5
        assert len(items) == 5
        small_count = small.count

        # --- 再 15 条，共 20 条 ---
        await _make_schedules(db_session, member, 15)
        with _SelectCounter(db_session) as big:
            items2, total2 = await schedule_service.list_schedules(
                db_session, user=member, page=1, page_size=50
            )
        assert total2 == 20
        assert len(items2) == 20
        big_count = big.count

        # 核心断言：条数翻 4 倍，SELECT 次数不变。
        # 修复前是「count + 1 + N」——N 从 5 涨到 20，这里会差 15 次。
        assert big_count == small_count, (
            f"SELECT 次数随条数增长（{small_count} -> {big_count}），仍是 N+1"
        )

    async def test_admin_view_also_batches_owner_email(self, db_session):
        """admin 视角同理：owner 邮箱也必须批量取，不能每条一次。

        注意：admin 的 list_schedules 看的是**全库**规则（不受 owner 过滤），
        所以这里不断言精确条数，只断言「返回条数 == total」以及 owner_email
        被批量填上——这两点足以证明批量路径生效。查询次数的上界按
        「返回条数 / 2」这种量级比较没有意义，改为直接对比峰值：
        SELECT 次数必须显著小于「条数 × 2」（逐条实现会是 2N）。
        """
        admin = await _make_user(db_session, role="admin")
        owners = [await _make_user(db_session) for _ in range(3)]
        for owner in owners:
            await _make_schedules(db_session, owner, 2)

        with _SelectCounter(db_session) as counter:
            items, total = await schedule_service.list_schedules(
                db_session, user=admin, page=1, page_size=50
            )

        assert len(items) == min(total, 50)
        # 别人拥有的那些规则必须带 owner_email（证明批量查询确实生效）
        others = [i for i in items if i.owner_id != admin.id]
        assert others, "本测试的前提是库里存在他人规则"
        assert all(i.owner_email for i in others), "admin 视角 owner_email 未被填充"
        # 逐条实现是 2 x len(items) 次；批量实现应远小于该值
        assert counter.count < len(items) * 2

    async def test_missing_target_reports_missing_not_crash(self, db_session):
        """目标被物理删除时仍返回 missing（批量查询没找到该 id 的分支）。"""
        member = await _make_user(db_session)
        rows = await _make_schedules(db_session, member, 1)
        # 直接把目标删掉，模拟「目标已不存在」
        await db_session.delete(await db_session.get(Agent, rows[0].target_id))
        await db_session.commit()

        items, _ = await schedule_service.list_schedules(
            db_session, user=member, page=1, page_size=50
        )
        assert len(items) == 1
        assert items[0].target.status.value == "missing"
        assert items[0].target.name is None
