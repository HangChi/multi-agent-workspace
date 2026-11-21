"""执行任务的落库与入队辅助单测（015 覆盖率补齐）。

`_persist_steps` 有一个很容易踩的分支：**预建的 running 步骤**。沙箱执行与外部
工具调用会先建一条 running 步骤，让子表（sandbox_executions）能引用它，然后由
各自的结果处理器决定最终状态。runner 事后回写时，如果无条件覆盖，就会把
「沙箱判定为 failed」的步骤改成「completed」——审计里就看不到失败了。

`_enqueue` 的取舍相反：入队失败只告警不抛。当前 Run 的结果不该因为「下一个阶段
没排上队」而失败。
"""

import uuid

import pytest
from sqlalchemy import event, select

from app.models.request import Request
from app.models.run import Run
from app.models.step import Step
from app.models.user import User
from app.tasks import execute_run as er

pytestmark = pytest.mark.integration


async def _run(db) -> Run:
    user = User(
        email=f"st-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="member",
        status="active",
    )
    db.add(user)
    await db.flush()
    request = Request(owner_id=user.id, title="步骤测试", status="running")
    db.add(request)
    await db.flush()
    run = Run(
        request_id=request.id,
        owner_id=user.id,
        assignee_type="agent",
        assignee_id=str(uuid.uuid4()),
        strategy="direct",
        status="running",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def test_persist_steps_creates_rows(db_session):
    run = await _run(db_session)
    steps = [
        {"seq": 1, "kind": "llm", "input": {"q": 1}, "status": "completed"},
        {"seq": 2, "kind": "tool", "tool_name": "calculate", "duration_ms": 12},
    ]

    await er._persist_steps(db_session, run, steps)
    await db_session.commit()

    rows = (
        await db_session.execute(
            Step.__table__.select().where(Step.run_id == run.id).order_by(Step.seq)
        )
    ).all()
    assert [row.seq for row in rows] == [1, 2]
    assert rows[1].tool_name == "calculate"
    # 步骤 id 被回填进 dict，供后续批次复用（避免 seq 重复）
    assert steps[0]["id"] and steps[1]["id"]


async def test_persist_steps_fills_precreated_running_step(db_session):
    """预建的 running 步骤要被填内容，而不是插一条新行。"""
    run = await _run(db_session)
    precreated = Step(
        run_id=run.id, seq=1, kind="tool", tool_name="run_python", status="running"
    )
    db_session.add(precreated)
    await db_session.commit()

    await er._persist_steps(
        db_session,
        run,
        [{"id": precreated.id, "seq": 1, "kind": "tool", "output": {"ok": True}}],
    )
    await db_session.commit()

    refreshed = await db_session.get(Step, precreated.id)
    assert refreshed.status == "completed"
    assert refreshed.output == {"ok": True}


async def test_persist_steps_keeps_finalized_handler_outcome(db_session):
    """已由专用处理器定案的步骤不能被 runner 覆盖回 completed。

    沙箱判定 failed，runner 的 step dict 并不知道，覆盖会让审计看不到失败。
    """
    run = await _run(db_session)
    finalized = Step(
        run_id=run.id, seq=1, kind="tool", tool_name="run_python", status="failed"
    )
    db_session.add(finalized)
    await db_session.commit()

    await er._persist_steps(
        db_session,
        run,
        [{"id": finalized.id, "seq": 1, "kind": "tool", "status": "completed"}],
    )
    await db_session.commit()

    refreshed = await db_session.get(Step, finalized.id)
    assert refreshed.status == "failed"


def test_enqueue_tolerates_broker_failure(monkeypatch):
    """入队失败只告警：当前 Run 的结果不该因为下一阶段没排上队而失败。"""

    def _boom(*args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(er.celery_app, "send_task", _boom)

    er._enqueue([str(uuid.uuid4())])  # 不抛即通过


def test_enqueue_sends_task(monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setattr(
        er.celery_app,
        "send_task",
        lambda name, args=None, **kw: sent.append((name, args)),
    )

    run_id = str(uuid.uuid4())
    er._enqueue([run_id])

    assert sent == [("app.tasks.execute_run.execute_run", [run_id])]


def test_enqueue_with_delay_passes_countdown(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        er.celery_app,
        "send_task",
        lambda name, args=None, **kw: captured.update(
            {"name": name, "countdown": kw.get("countdown")}
        ),
    )

    er._enqueue_with_delay(str(uuid.uuid4()), 30)

    assert captured["countdown"] == 30


async def test_is_cancelled_reads_committed_status(db_session):
    run = await _run(db_session)
    assert await er._is_cancelled(db_session, run.id) is False

    run.status = "cancelled"
    await db_session.commit()
    assert await er._is_cancelled(db_session, run.id) is True


# ---------- P3：_load_context 不再白加载历史 steps ----------


async def test_load_context_returns_last_seq_without_loading_steps(db_session):
    """`_load_context` 顺带返回续写起点 seq，且不把历史 steps 塞进 identity map。

    P3 背景：此前 `selectinload(Run.steps)` 白加载整段历史——下游从不读
    run.steps，而 step.output 可能含大段 sandbox stdout；重试/恢复一个跑了
    多步的 Run，每次都会把这些全量反序列化。续写序号另有 max(seq) 查询。
    现在把 max(seq) 合并进 _load_context，并去掉那次无用的 selectinload。
    """
    run = await _run(db_session)

    # 造 3 条历史步骤，seq 1..3
    for seq in (1, 2, 3):
        db_session.add(
            Step(
                run_id=run.id,
                seq=seq,
                kind="llm",
                input={"messages": []},
                output={"text": "x" * 1000},
                status="completed",
            )
        )
    await db_session.commit()

    # 清掉 identity map，确保断言针对真实的加载行为
    db_session.expunge_all()

    loaded_run, agent, request, last_seq = await er._load_context(
        db_session, str(run.id)
    )

    assert last_seq == 3  # 续写起点正确
    assert loaded_run.id == run.id
    assert request.id == run.request_id
    assert agent is None  # assignee_id 是个不存在的 uuid

    # 关键：steps 没有被预加载（访问才会触发惰性查询/报错）
    # 用 inspect 的 unloaded 集合判断，不触发 IO
    from sqlalchemy import inspect

    state = inspect(loaded_run)
    assert "steps" in state.unloaded, "steps 不该被 selectinload 预加载"


async def test_load_context_last_seq_is_zero_for_fresh_run(db_session):
    """新 Run（无历史步骤）续写起点是 0。"""
    run = await _run(db_session)
    db_session.expunge_all()
    _, _, _, last_seq = await er._load_context(db_session, str(run.id))
    assert last_seq == 0


# ---------- P2：_persist_steps 的 SELECT 次数不随步数增长 ----------


class _SelectCounter:
    """统计一个 session 上发出的 SELECT 次数（P2 与 P1 用同一种手段）。

    N+1 是纯性能问题：结果集本来就正确，只有数查询次数才能验证修复。
    """

    def __init__(self, session):
        self.session = session
        self.count = 0

    def _before(self, conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            self.count += 1

    def __enter__(self):
        event.listen(
            self.session.sync_session.bind, "before_cursor_execute", self._before
        )
        return self

    def __exit__(self, *exc):
        event.remove(
            self.session.sync_session.bind, "before_cursor_execute", self._before
        )
        return False


async def test_persist_steps_skips_select_for_new_steps(db_session):
    """不带 id 的步骤必然是新行，一条 SELECT 都不该发。

    P2 背景：此前对每条 `await db.get(Step, step["id"])` 都查一次库。runner
    现场生成的 LLM / final 步骤从来不带 id，等于为「确认它不存在」白跑往返。
    """
    run = await _run(db_session)
    steps = [
        {"seq": 1, "kind": "llm", "status": "completed"},
        {"seq": 2, "kind": "final", "status": "completed"},
    ]

    with _SelectCounter(db_session) as counter:
        await er._persist_steps(db_session, run, steps)
        await db_session.commit()

    assert counter.count == 0, f"新建步骤不该查库，实际发了 {counter.count} 次 SELECT"
    # id 仍然被回填，后续批次复用同一身份而不是重复插入
    assert all(step["id"] for step in steps)


async def test_persist_steps_selects_once_for_precreated_batch(db_session):
    """一批预建（带 id）步骤只发 1 次 SELECT，而不是每条一次。

    刻意 `expunge_all()`：预建行若是本进程 `db.add` 的，旧实现的 `db.get`
    会命中 identity map 而**不发 SQL**，N+1 被掩盖。真正咬人的场景是
    FR-021 恢复执行——needs_input 挂起后由另一个 Worker 续跑，session 全新，
    预建行不在缓存里，逐条 `db.get` 就是逐条 SELECT。
    """
    run = await _run(db_session)
    precreated_ids = []
    for seq in range(1, 11):
        row = Step(run_id=run.id, seq=seq, kind="tool", tool_name="run_python")
        row.status = "running"
        db_session.add(row)
        await db_session.flush()
        precreated_ids.append((row.id, seq))
    await db_session.commit()
    db_session.expunge_all()  # 模拟新 Worker 的空白 session

    steps = [
        {"id": step_id, "seq": seq, "kind": "tool", "output": {"ok": True}}
        for step_id, seq in precreated_ids
    ]

    with _SelectCounter(db_session) as counter:
        await er._persist_steps(db_session, run, steps)
        await db_session.commit()

    assert counter.count == 1, f"10 条预建步骤应只查 1 次，实际 {counter.count} 次"
    refreshed = (
        (await db_session.execute(select(Step).where(Step.run_id == run.id)))
        .scalars()
        .all()
    )
    assert len(refreshed) == 10, "不应插入重复行"
    assert all(row.status == "completed" for row in refreshed)


# ---------- D6：执行锁只释放自己持有的那份 ----------


@pytest.fixture
def redis_client():
    """真连本机 Redis：CAS 只有在真 Redis 上才能验证（Lua 语义）。

    用独立的 key 前缀（d6-*）并由用例自行清理，避免污染开发环境的库。
    """
    import redis as redis_lib

    from app.core.config import settings

    client = redis_lib.from_url(settings.redis_url, decode_responses=True)
    try:
        client.ping()
    except Exception:  # noqa: BLE001
        client.close()
        pytest.skip("本机 Redis 不可用，跳过锁所有权用例")
    try:
        yield client
    finally:
        for key in client.scan_iter("run:lock:d6-*"):
            client.delete(key)
        client.close()


class TestRunLockOwnership:
    """`_release_lock` 必须是 compare-and-delete。

    D6 背景：原先无条件 `DELETE`。Worker 被 SIGKILL 卡过 TTL（3600s）后恢复时，
    锁早已被别的 Worker 重新获取，它却会把**别人的锁**删掉 → 第三个 Worker
    又能拿到 → 同一 Run 并发执行（seq 唯一约束冲突、成本重复累加）。
    """

    def test_release_removes_own_lock(self, redis_client):
        run_id = f"d6-{uuid.uuid4().hex[:8]}"
        token = er._acquire_lock(run_id)
        assert token is not None
        assert redis_client.get(f"run:lock:{run_id}") == token

        er._release_lock(run_id, token)
        assert redis_client.get(f"run:lock:{run_id}") is None

    def test_release_does_not_delete_someone_elses_lock(self, redis_client):
        """核心用例：A 超时后 B 拿到锁，A 的释放不能删掉 B 的锁。"""
        run_id = f"d6-{uuid.uuid4().hex[:8]}"

        # A 拿锁后被卡住（模拟 SIGKILL 卡过 TTL），锁过期，B 重新获取
        token_a = er._acquire_lock(run_id)
        assert token_a is not None
        redis_client.delete(f"run:lock:{run_id}")  # 模拟 TTL 到期
        token_b = er._acquire_lock(run_id)
        assert token_b is not None and token_b != token_a

        # A 恢复后释放：必须无效
        er._release_lock(run_id, token_a)
        assert redis_client.get(f"run:lock:{run_id}") == token_b, (
            "A 的释放删掉了 B 的锁——第三个 Worker 会拿到锁并并发执行同一个 Run"
        )

        # B 自己释放才有效
        er._release_lock(run_id, token_b)
        assert redis_client.get(f"run:lock:{run_id}") is None

    def test_second_acquire_fails_while_held(self, redis_client):
        """同一 Run 重复获取返回 None（替代原来 bool 语义的回归保护）。"""
        run_id = f"d6-{uuid.uuid4().hex[:8]}"
        assert er._acquire_lock(run_id) is not None
        assert er._acquire_lock(run_id) is None
        redis_client.delete(f"run:lock:{run_id}")

    def test_release_without_token_is_noop(self, redis_client):
        """拿不到锁时 token 为 None，finally 里的释放必须安全跳过。"""
        run_id = f"d6-{uuid.uuid4().hex[:8]}"
        redis_client.set(f"run:lock:{run_id}", "someone-else")
        er._release_lock(run_id, None)
        assert redis_client.get(f"run:lock:{run_id}") == "someone-else"
        redis_client.delete(f"run:lock:{run_id}")
