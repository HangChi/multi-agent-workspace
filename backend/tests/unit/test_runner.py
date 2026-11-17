"""执行器单元测试（T062/T075，US5）：ReAct 循环全路径（FR-014/FR-019/FR-021）。

对齐 tasks.md T068（runner.py 消息序列循环）与 R-04 成本熔断：
- LLM 直接 final → 完成
- tool_call → 执行器执行 → 回填 → 再 LLM → final
- max_steps 超限 → 失败终止
- 超时 → timeout 终止
- 成本熔断：cost > limit → 失败终止（SC-006，T075）
- needs_input 触发 → 挂起等待回复
- 异常转可读错误

纯单元测试：注入 FakeLLM / FakeExecutor / 内存回调，不依赖 DB。
"""

from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

import pytest

from app.orchestrator.runner import (
    CostLimitExceeded,
    ExecutionTimeout,
    MaxStepsExceeded,
    NeedsInput,
    ReActRunner,
)
from app.orchestrator.state_machine import RunStatus

# ---------- 假对象 ----------


@dataclass
class FakeToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeLLMResponse:
    """模拟 LiteLLM 响应：content + tool_calls + usage→cost。"""

    content: str = ""
    tool_calls: list[FakeToolCall] = field(default_factory=list)
    cost_cents: int = 0
    needs_input: bool = False
    needs_input_message: str = ""


class FakeLLM:
    """脚本化 LLM：按序列返回预设响应。"""

    def __init__(self, responses: list[FakeLLMResponse]):
        self.responses = list(responses)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> FakeLLMResponse:
        self.calls.append(messages)
        if not self.responses:
            raise RuntimeError("脚本耗尽：测试预期之外的 LLM 调用")
        return self.responses.pop(0)


@dataclass
class FakeExecutorResult:
    content: str


class FakeExecutor:
    def __init__(self, result: str = "工具结果"):
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "搜索",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> FakeExecutorResult:
        self.calls.append(arguments)
        return FakeExecutorResult(content=self.result)


@dataclass
class FakeRun:
    """模拟 Run：runner 只通过回调写状态。"""

    id: str = "r1"
    status: str = "queued"
    current_step: int = 0
    cost_cents: int = 0
    error: str | None = None
    result: dict[str, Any] | None = None
    started_at: Any = None
    finished_at: Any = None

    def mark_started(self) -> None:
        from datetime import datetime

        self.started_at = datetime.now(UTC)

    def mark_finished(self) -> None:
        from datetime import datetime

        self.finished_at = datetime.now(UTC)

    def clear_execution(self) -> None:
        self.current_step = 0
        self.cost_cents = 0
        self.error = None
        self.result = None
        self.started_at = None
        self.finished_at = None


class Sink:
    """内存持久化/事件回调：记录 runner 的全部副作用（回调为 awaitable）。"""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.statuses: list[str] = []
        self.costs: list[int] = []

    async def save_step(self, step: dict[str, Any]) -> None:
        self.steps.append(step)

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def set_status(self, status: str) -> None:
        self.statuses.append(status)

    async def set_cost(self, cost_cents: int) -> None:
        self.costs.append(cost_cents)


def _make_runner(
    llm: FakeLLM,
    *,
    executors: dict[str, FakeExecutor] | None = None,
    max_steps: int = 10,
    timeout_seconds: int = 1800,
    cost_limit_cents: int = 10000,
    sink: Sink | None = None,
) -> tuple[ReActRunner, FakeRun, Sink]:
    runner = ReActRunner(
        llm=llm,
        executors=executors or {},
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
        cost_limit_cents=cost_limit_cents,
    )
    run = FakeRun()
    sink = sink or Sink()
    return runner, run, sink


async def _run(runner: ReActRunner, run: FakeRun, sink: Sink) -> str:
    """执行并返回最终状态。"""
    status = await runner.run(
        run=run,
        request_text="帮我调研一下 X",
        save_step=sink.save_step,
        publish_event=sink.publish,
        set_status=sink.set_status,
        set_cost=sink.set_cost,
    )
    return status


# ---------- LLM 直接 final ----------


async def test_llm_direct_final():
    """LLM 直接出结果 → completed，写 final 步骤。"""
    llm = FakeLLM([FakeLLMResponse(content="最终答案", cost_cents=5)])
    runner, run, sink = _make_runner(llm)
    status = await _run(runner, run, sink)

    assert status == RunStatus.COMPLETED.value
    assert run.status == "completed"
    assert run.result == {"answer": "最终答案"}
    # 一个 llm 步骤 + 一个 final 步骤
    kinds = [s["kind"] for s in sink.steps]
    assert kinds == ["llm", "final"]
    assert run.cost_cents == 5
    assert sink.costs == [5]
    # 事件：status_change(running) → step_new×2 → step_update×2 → cost_update → run_completed
    types = [e["type"] for e in sink.events]
    assert types[0] == "status_change" and types[0] in types
    assert "run_completed" in types
    assert "step_new" in types and "step_update" in types
    assert "cost_update" in types


# ---------- tool_call → 执行 → 回填 → final ----------


async def test_tool_call_then_final():
    """tool_call → 执行器 → 回填 → 再 LLM → final。"""
    web = FakeExecutor(result="搜索到：腾讯云 2025 年报营收 8000 亿")
    llm = FakeLLM(
        [
            FakeLLMResponse(
                tool_calls=[FakeToolCall("web_search", {"query": "腾讯云 2025"})],
                cost_cents=3,
            ),
            FakeLLMResponse(content="根据搜索结果，营收 8000 亿", cost_cents=4),
        ]
    )
    runner, run, sink = _make_runner(llm, executors={"web_search": web})
    status = await _run(runner, run, sink)

    assert status == RunStatus.COMPLETED.value
    # 执行器被调用且收到参数
    assert web.calls == [{"query": "腾讯云 2025"}]
    # 步骤序列：llm → tool → llm → final
    kinds = [s["kind"] for s in sink.steps]
    assert kinds == ["llm", "tool", "llm", "final"]
    # 回填内容进入第二次 LLM 调用消息序列
    assert len(llm.calls) == 2
    tool_result_msg = llm.calls[1][-1]
    assert "搜索到：腾讯云 2025 年报营收 8000 亿" in tool_result_msg["content"]
    assert run.cost_cents == 7  # 3 + 4
    # tool 步骤记录了 tool_name
    assert sink.steps[1]["tool_name"] == "web_search"
    assert sink.steps[1]["output"] == {
        "content": "搜索到：腾讯云 2025 年报营收 8000 亿"
    }


async def test_unknown_tool_returns_error_to_llm():
    """未知工具：不中断，向 LLM 回填错误说明后继续。"""
    llm = FakeLLM(
        [
            FakeLLMResponse(
                tool_calls=[FakeToolCall("nonexistent_tool", {})], cost_cents=1
            ),
            FakeLLMResponse(content="工具不存在，我改用其他方式", cost_cents=2),
        ]
    )
    runner, run, sink = _make_runner(llm, executors={})
    status = await _run(runner, run, sink)

    assert status == RunStatus.COMPLETED.value
    # 第二次 LLM 调用收到了「未知工具」回填
    last_msg = llm.calls[1][-1]
    assert "未知工具" in last_msg["content"]
    assert "nonexistent_tool" in last_msg["content"]


# ---------- max_steps 超限 ----------


async def test_max_steps_exceeded_fails():
    """LLM 反复调工具不收敛 → max_steps 超限 → failed。"""
    llm = FakeLLM(
        [FakeLLMResponse(tool_calls=[FakeToolCall("web_search", {})], cost_cents=1)]
        * 11
    )
    runner, run, sink = _make_runner(
        llm, executors={"web_search": FakeExecutor()}, max_steps=10
    )
    with pytest.raises(MaxStepsExceeded):
        await _run(runner, run, sink)

    assert run.status == "failed"
    assert run.error is not None
    assert "超过最大步骤数" in run.error


# ---------- 超时 ----------


async def test_timeout_terminates():
    """运行超时 → timeout 状态。"""
    llm = FakeLLM(
        [FakeLLMResponse(tool_calls=[FakeToolCall("web_search", {})], cost_cents=1)] * 5
    )

    class SlowLLM(FakeLLM):
        """每次调用都睡 0.2s，叠加后超过 timeout_seconds=0.3。"""

        async def chat(
            self, messages: list[dict[str, Any]], **kwargs: Any
        ) -> FakeLLMResponse:
            import asyncio

            await asyncio.sleep(0.2)
            return await super().chat(messages, **kwargs)

    runner, run, sink = _make_runner(
        SlowLLM(llm.responses),
        executors={"web_search": FakeExecutor()},
        timeout_seconds=0.3,
    )
    with pytest.raises(ExecutionTimeout):
        await _run(runner, run, sink)

    assert run.status == "timeout"


# ---------- 成本熔断（T075 / SC-006） ----------


async def test_cost_breaker_terminates():
    """累计成本超过 cost_limit → 终止为 failed（SC-006）。"""
    # 每次调用 60 分，limit 100 分：第 2 次后 120 > 100 → 熔断
    llm = FakeLLM(
        [
            FakeLLMResponse(tool_calls=[FakeToolCall("web_search", {})], cost_cents=60),
            FakeLLMResponse(tool_calls=[FakeToolCall("web_search", {})], cost_cents=60),
            FakeLLMResponse(content="本不该执行到这", cost_cents=1),
        ]
    )
    runner, run, sink = _make_runner(
        llm, executors={"web_search": FakeExecutor()}, cost_limit_cents=100
    )
    with pytest.raises(CostLimitExceeded):
        await _run(runner, run, sink)

    # 成本熔断是明确的可读失败
    assert run.status == "failed"
    assert "成本" in run.error
    assert run.cost_cents == 120
    # LLM 只被调用 2 次（第 3 次被熔断拦下）
    assert len(llm.calls) == 2


async def test_cost_resumes_from_persisted_total():
    """恢复执行/路由已计费时，成本从已落库值续算（熔断不可被重置绕过）。"""
    llm = FakeLLM(
        [
            FakeLLMResponse(tool_calls=[FakeToolCall("web_search", {})], cost_cents=60),
            FakeLLMResponse(content="本不该执行到这", cost_cents=1),
        ]
    )
    runner, run, sink = _make_runner(
        llm, executors={"web_search": FakeExecutor()}, cost_limit_cents=100
    )
    # router 决策与上一次 needs_input 段落已经花了 50 分并落库
    run.cost_cents = 50

    with pytest.raises(CostLimitExceeded):
        await _run(runner, run, sink)

    # 50 + 60 = 110 > 100，第一次 LLM 调用后即熔断，且累计值不被清零
    assert run.cost_cents == 110
    assert sink.costs == [110]
    assert len(llm.calls) == 1


async def test_cost_exactly_at_limit_allowed():
    """成本恰好等于上限：不熔断（> limit 才终止）。"""
    llm = FakeLLM([FakeLLMResponse(content="完成", cost_cents=100)])
    runner, run, sink = _make_runner(llm, cost_limit_cents=100)
    status = await _run(runner, run, sink)
    assert status == RunStatus.COMPLETED.value


# ---------- needs_input 触发 ----------


async def test_needs_input_suspends():
    """LLM 请求补充信息 → needs_input 挂起（FR-021）。"""
    llm = FakeLLM(
        [
            FakeLLMResponse(
                content="",
                needs_input=True,
                needs_input_message="需要补充信息: 请提供目标客户名单",
                cost_cents=2,
            )
        ]
    )
    runner, run, sink = _make_runner(llm)
    with pytest.raises(NeedsInput):
        await _run(runner, run, sink)

    assert run.status == "needs_input"
    # 发布了 needs_input 事件
    assert any(e["type"] == "needs_input" for e in sink.events)


# ---------- 异常转可读错误 ----------


async def test_llm_exception_becomes_readable_error():
    """LLM 抛异常 → 捕获并转为可读错误，run 标记 failed。"""

    class ExplodingLLM:
        async def chat(
            self, messages: list[dict[str, Any]], **kwargs: Any
        ) -> FakeLLMResponse:
            raise ConnectionError("连接被拒绝")

    runner, run, sink = _make_runner(ExplodingLLM())  # type: ignore[arg-type]
    status = await _run(runner, run, sink)

    assert status == RunStatus.FAILED.value
    assert run.status == "failed"
    assert "连接被拒绝" in run.error
    assert "LLM 调用失败" in run.error or run.error is not None


async def test_tool_receives_cancellation_probe_and_run_timeout_cap():
    """Sandbox-capable executors receive a thread-safe cancel probe and remaining budget."""
    import asyncio

    class SandboxLikeExecutor(FakeExecutor):
        def __init__(self):
            super().__init__()
            self.abort_check = None
            self.timeout_cap = None
            self.prepared = False

        def set_abort_check(self, check):
            self.abort_check = check

        def set_timeout_cap(self, timeout):
            self.timeout_cap = timeout

        def prepare_execution(self):
            self.prepared = True

        async def execute(self, arguments):
            return {"aborted": await asyncio.to_thread(self.abort_check)}

    executor = SandboxLikeExecutor()
    llm = FakeLLM(
        [
            FakeLLMResponse(
                tool_calls=[FakeToolCall("run_python", {"code": "print(1)"})]
            ),
            FakeLLMResponse(content="完成"),
        ]
    )
    runner, run, sink = _make_runner(llm, executors={"run_python": executor})
    status = await runner.run(
        run=run,
        request_text="test",
        save_step=sink.save_step,
        publish_event=sink.publish,
        set_status=sink.set_status,
        set_cost=sink.set_cost,
        should_abort=lambda: _return_false(),
    )

    assert status == RunStatus.COMPLETED.value
    assert executor.prepared
    assert (
        executor.timeout_cap is not None
        and 1 <= executor.timeout_cap <= runner.timeout_seconds
    )
    assert sink.steps[1]["output"] == {"aborted": False}


async def test_sandbox_lifecycle_callbacks_bracket_tool_execution():
    """Persisted sandbox identifiers and callbacks bracket execution in order."""

    class SandboxExecutor(FakeExecutor):
        language = "python"
        argument_name = "code"

        def prepare_execution(self, arguments):
            assert arguments == {"code": "print(1)"}
            return "execution-1", {"timeout_s": 30}

        async def execute(self, arguments):
            order.append("execute")
            return {
                "ok": True,
                "status": "ok",
                "stdout": "1",
                "_sandbox_payload": {
                    "execution_id": "execution-1",
                    "language": "python",
                    "code": arguments["code"],
                    "limits": {"timeout_s": 30},
                    "artifacts": [{"name": "out.txt", "size": 4, "data": b"data"}],
                    "artifact_truncated": {"reason": "test"},
                    "egress_accesses": [{"target_ip": "203.0.113.1", "port": 443}],
                    "policy_denied": True,
                },
            }

    order = []
    starts = []
    finishes = []
    executor = SandboxExecutor()
    llm = FakeLLM(
        [
            FakeLLMResponse(
                tool_calls=[FakeToolCall("run_python", {"code": "print(1)"})]
            ),
            FakeLLMResponse(content="done"),
        ]
    )
    runner, run, sink = _make_runner(llm, executors={"run_python": executor})

    async def started(detail):
        order.append("started")
        starts.append(detail)

    async def finished(detail):
        order.append("finished")
        finishes.append(detail)

    runner.on_sandbox_started = started
    runner.on_sandbox_result = finished
    await _run(runner, run, sink)

    assert order == ["started", "execute", "finished"]
    assert starts[0]["execution_id"] == finishes[0]["payload"]["execution_id"]
    assert starts[0]["step_id"] == finishes[0]["step_id"] == sink.steps[1]["id"]
    assert sink.steps[1]["output"] == {"ok": True, "status": "ok"}
    assert finishes[0]["payload"]["artifacts"] == [
        {"name": "out.txt", "size": 4, "data": b"data"}
    ]
    assert finishes[0]["payload"]["artifact_truncated"] == {"reason": "test"}
    assert finishes[0]["payload"]["egress_accesses"] == [
        {"target_ip": "203.0.113.1", "port": 443}
    ]
    assert finishes[0]["payload"]["policy_denied"] is True


async def _return_false() -> bool:
    return False


# ---------- 外部工具：step 行必须先于调用记录存在 ----------


class _ExternalExecutor:
    """最小外部执行器：记录「被调用时」看到的 invocation step id。"""

    is_external_tool = True
    version = "1.0.0+digest"

    def __init__(self, order: list[str]):
        self.order = order
        self.invocation_step_id: str | None = None

    def set_invocation_context(self, step_id: str) -> None:
        # The real executor stores this and stamps every invocation row with it.
        self.invocation_step_id = step_id

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "mcp_search_v1",
                "description": "search",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    async def execute(self, arguments: dict[str, Any]):
        # 调用记录在 execute 内部落库，此时 step 行必须已存在。
        self.order.append("execute")
        return {"content": [{"type": "text", "text": "hit"}]}


async def test_external_tool_step_is_created_before_the_executor_runs():
    """on_external_started 必须先于 execute：调用记录以 step_id 为外键。"""
    order: list[str] = []
    executor = _ExternalExecutor(order)
    llm = FakeLLM(
        [
            FakeLLMResponse(tool_calls=[FakeToolCall("mcp_search_v1", {"q": "x"})]),
            FakeLLMResponse(content="done"),
        ]
    )
    runner, run, sink = _make_runner(llm, executors={"mcp_search_v1": executor})
    started: list[dict] = []

    async def on_external_started(detail):
        order.append("started")
        started.append(detail)

    runner.on_external_started = on_external_started
    assert await _run(runner, run, sink) == RunStatus.COMPLETED.value

    assert order == ["started", "execute"]
    assert started[0]["step_id"] == executor.invocation_step_id
    assert started[0]["step_id"] == sink.steps[1]["id"]
    assert started[0]["step_seq"] == sink.steps[1]["seq"]
    assert started[0]["stable_name"] == "mcp_search_v1"
    assert started[0]["version"] == "1.0.0+digest"
    assert sink.steps[1]["tool_name"] == "mcp_search_v1"
    assert sink.steps[1]["status"] == "completed"
