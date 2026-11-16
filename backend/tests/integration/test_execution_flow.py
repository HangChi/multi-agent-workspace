"""完整闭环集成测试（T076，US5）：分配→入队→执行→完成（SC-004）。

对齐 tasks.md T076 与 SC-004：mock LLM 走完
「需求创建 → 拖拽分配 → 入队（queued）→ 执行（_execute）→ 完成（completed）」，
断言 steps / result / cost / WS 事件，并验证从分配到完成耗时 < 5 分钟（SC-004）。

执行链真实路径：
- 分配端点（POST /requests/{id}/assign）→ 真实建 Run（queued）→ 真实 DB 落库
- 执行用 app.tasks.execute_run._execute（TESTING=1 时 execute_run 顶层短路，
  直接驱动其内部核心执行链 = Worker 消费路径的等价物）
- LLM 通过 monkeypatch LiteLLMClient.chat 注入脚本化响应（真实 runner 循环）
- WS 事件走真实 Redis（run:{id} 频道），用 subscribe_run_events 收集断言
"""

import asyncio
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.core.events import subscribe_run_events
from app.executors.llm import LLMResponse, ToolCall
from app.models.agent import Agent
from app.models.run import Run
from app.models.step import Step
from app.orchestrator.runner import ReActRunner
from app.orchestrator.state_machine import RunStatus
from app.tasks.execute_run import _execute

REQUESTS_URL = "/api/v1/requests"
AGENTS_URL = "/api/v1/agents"
RUNS_URL = "/api/v1/runs"


async def _create_agent(client, headers: dict, *, cost_limit_cents: int = 10000) -> dict:
    resp = await client.post(
        AGENTS_URL,
        headers=headers,
        json={
            "name": f"flow-agent-{uuid.uuid4().hex[:6]}",
            "system_prompt": "you are a helpful assistant",
            "model": "gpt-4o-mini",
            "cost_limit_cents": cost_limit_cents,
            "max_steps": 10,
            "tools": ["web_search"],
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _create_request(client, headers: dict, *, title: str = "闭环执行") -> dict:
    resp = await client.post(
        REQUESTS_URL,
        headers=headers,
        json={
            "title": f"{title}-{uuid.uuid4().hex[:6]}",
            "description": "帮我调研并整理一份简短报告",
            "expected_output": "一段 3-5 行的总结",
            "priority": "normal",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _assign(client, headers: dict, request_id: str, agent_id: str) -> dict:
    resp = await client.post(
        f"{REQUESTS_URL}/{request_id}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent_id},
    )
    assert resp.status_code == 202
    return resp.json()


async def _load_and_execute(db_session, run_id: str, agent_id: str) -> Run:
    """加载 Run + Agent 并驱动 _execute（等价 Worker 消费路径）。"""
    run = await db_session.get(Run, run_id)
    assert run is not None
    agent = await db_session.get(Agent, agent_id)
    assert agent is not None
    await _execute(db_session, run, agent)
    await db_session.refresh(run)
    return run


class ScriptedLLM:
    """脚本化 LLM：按序列返回预设响应（等价真实 LiteLLMClient.chat 签名）。"""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[tuple[list[dict[str, Any]], Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.calls.append((messages, kwargs))
        if not self.responses:
            raise RuntimeError("脚本耗尽：测试预期之外的 LLM 调用")
        return self.responses.pop(0)


@pytest.fixture
def _fake_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """把 LiteLLMClient.chat 替换为脚本化 LLM（真实 runner 循环使用）。"""
    responses = [
        # 第 1 次：请求工具（搜索）
        LLMResponse(
            content="",
            tool_calls=[ToolCall(name="web_search", arguments={"query": "腾讯云 2025 年报"})],
            cost_cents=3,
        ),
        # 第 2 次：给出最终答案
        LLMResponse(content="根据搜索结果，腾讯云 2025 年营收约 8000 亿元。", cost_cents=4),
    ]
    fake = ScriptedLLM(responses)
    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", fake.chat)
    return fake


@pytest.fixture
def _fake_llm_skipped(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """无响应脚本：任何调用都会抛错（用于验证执行失败路径）。"""
    fake = ScriptedLLM([])
    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", fake.chat)
    return fake


async def _collect_events(run_id: str, *, timeout: float = 5.0) -> list[dict[str, Any]]:
    """订阅 run:{id} 频道，收集直到终态事件或超时。"""
    events: list[dict[str, Any]] = []
    stop = asyncio.Event()

    async def handler(payload: dict[str, Any]) -> None:
        events.append(payload)
        if payload.get("type") in {"run_completed", "run_failed", "run_timeout"}:
            stop.set()

    task = asyncio.create_task(subscribe_run_events(run_id, handler))
    try:
        await asyncio.wait_for(stop.wait(), timeout=timeout)
    except TimeoutError:
        pass
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: S110, BLE001 - 测试清理，静默即可
            pass
    return events


# ---------- 闭环：分配 → 入队 → 执行 → 完成 ----------


async def test_terminal_state_is_not_visible_before_all_steps_are_committed(
    client, make_headers, db_session, _fake_llm, monkeypatch
):
    headers = await make_headers()
    agent = await _create_agent(client, headers)
    request = await _create_request(client, headers)
    assigned = await _assign(client, headers, request["id"], agent["id"])
    run_id = assigned["run_id"]
    snapshots = []
    original = ReActRunner.run

    async def observe_runner_return(self, **kwargs):
        result = await original(self, **kwargs)
        # Read through another real API/DB session at the precise window before
        # _execute's final batch persistence, not after the Worker has returned.
        response = await client.get(f"{RUNS_URL}/{run_id}", headers=headers)
        assert response.status_code == 200
        snapshots.append(response.json())
        return result

    monkeypatch.setattr(ReActRunner, "run", observe_runner_return)
    await _load_and_execute(db_session, run_id, agent["id"])
    assert snapshots[0]["status"] == "completed"
    assert [step["kind"] for step in snapshots[0]["steps"]] == ["llm", "tool", "llm", "final"]
    final = (await client.get(f"{RUNS_URL}/{run_id}", headers=headers)).json()
    assert [step["id"] for step in final["steps"]] == [step["id"] for step in snapshots[0]["steps"]]


async def test_full_flow_assign_execute_complete(client, make_headers, db_session, _fake_llm):
    """完整闭环：需求创建 → 拖拽分配（queued）→ 执行 → completed（SC-004）。"""

    headers = await make_headers(role="member")
    start = time.monotonic()

    # 1. 创建需求 + Agent
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)

    # 2. 拖拽分配 → 入队（queued）
    assigned = await _assign(client, headers, req["id"], agent["id"])
    run_id = assigned["run_id"]
    assert assigned["status"] == "queued"

    # 3. 执行（mock LLM），同时订阅 WS 事件
    events_task = asyncio.create_task(_collect_events(run_id))
    run = await _load_and_execute(db_session, run_id, agent["id"])
    events = await events_task

    # 4. 状态机终态 + 结果
    assert run.status == RunStatus.COMPLETED.value
    assert run.result == {"answer": "根据搜索结果，腾讯云 2025 年营收约 8000 亿元。"}
    assert run.finished_at is not None

    # 5. 成本累计（3 + 4 = 7 分）
    assert run.cost_cents == 7

    # 6. steps 落库：llm → tool → llm → final
    rows = (
        await db_session.execute(
            select(Step).where(Step.run_id == run_id).order_by(Step.seq)
        )
    ).scalars().all()
    kinds = [s.kind for s in rows]
    assert kinds == ["llm", "tool", "llm", "final"]
    assert rows[1].tool_name == "web_search"
    assert rows[1].output["query"] == "腾讯云 2025 年报"
    assert rows[0].cost_cents == 3
    assert rows[2].cost_cents == 4
    assert [s.seq for s in rows] == [1, 2, 3, 4]  # seq 连续无缺

    # 7. current_step 随执行推进（契约字段，前端「当前步骤」展示用）
    await db_session.refresh(run)
    assert run.current_step == 4

    # 7. WS 事件（真实 Redis 频道）到达：… → run_completed
    types = [e["type"] for e in events]
    assert "status_change" in types
    assert "cost_update" in types
    assert "step_new" in types and "step_update" in types
    assert types[-1] == "run_completed"
    assert events[-1]["result"] == run.result

    # 8. SC-004：从拖拽到完成 < 5 分钟
    elapsed = time.monotonic() - start
    assert elapsed < 300, f"闭环耗时 {elapsed:.2f}s 超出 SC-004 阈值 5min"

    # 9. 详情端点能读到完整闭环产物
    detail = await client.get(f"{RUNS_URL}/{run_id}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "completed"
    assert body["cost_cents"] == 7
    assert len(body["steps"]) == 4
    assert body["result"] == run.result
    assert "artifacts" in body


# ---------- 闭环：执行失败 → 重试 → 成功（SC-008 前置）----------


async def test_full_flow_fail_then_retry_succeeds(
    client, make_headers, db_session, _fake_llm_skipped, monkeypatch
):
    """失败 → retry → 成功：验证重试路径（SC-008 基础）。"""

    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)
    assigned = await _assign(client, headers, req["id"], agent["id"])
    run_id = assigned["run_id"]

    # 第一次执行：LLM 脚本为空 → runner 捕获异常 → 可读失败（FR-017）
    run = await _load_and_execute(db_session, run_id, agent["id"])
    assert run.status == RunStatus.FAILED.value
    assert run.error is not None
    assert "LLM 调用失败" in run.error

    # 重试：POST /runs/{id}/retry → queued + 重置执行现场
    resp = await client.post(f"{RUNS_URL}/{run_id}/retry", headers=headers)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    await db_session.refresh(run)
    assert run.status == RunStatus.QUEUED.value
    assert run.cost_cents == 0
    assert run.error is None
    assert run.current_step == 0

    # 第二次执行：换成正常脚本 → 完成
    good_llm = ScriptedLLM([LLMResponse(content="重试成功，产出最终答案。", cost_cents=2)])
    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", good_llm.chat)
    run = await _load_and_execute(db_session, run_id, agent["id"])
    assert run.status == RunStatus.COMPLETED.value
    assert run.result == {"answer": "重试成功，产出最终答案。"}
    assert run.cost_cents == 2

    # 详情端点为可读失败 → 重试后 completed
    detail = await client.get(f"{RUNS_URL}/{run_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"


# ---------- 闭环：needs_input 挂起 → reply 恢复（FR-021）----------


async def test_full_flow_needs_input_then_reply(
    client, make_headers, db_session, monkeypatch
):
    """LLM 请求补充信息 → needs_input 挂起 → reply → 恢复完成（FR-021）。"""

    headers = await make_headers(role="member")
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)
    assigned = await _assign(client, headers, req["id"], agent["id"])
    run_id = assigned["run_id"]

    suspending_llm = ScriptedLLM(
        [
            LLMResponse(
                content="",
                needs_input=True,
                needs_input_message="请提供目标客户名单",
                cost_cents=2,
            )
        ]
    )
    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", suspending_llm.chat)

    events_task = asyncio.create_task(_collect_events(run_id))
    run = await _load_and_execute(db_session, run_id, agent["id"])
    events = await events_task

    assert run.status == RunStatus.NEEDS_INPUT.value
    assert any(e["type"] == "needs_input" for e in events)

    # reply 恢复
    resp = await client.post(
        f"{RUNS_URL}/{run_id}/reply", headers=headers, json={"message": "客户名单：ABC 公司"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    await db_session.refresh(run)
    assert run.status == RunStatus.RUNNING.value
    assert run.user_reply == "客户名单：ABC 公司"

    # Worker 消费恢复任务后，回复必须被清空：否则再次挂起时同一句话会被
    # 当作新的补充信息重新注入，模型会把旧回答再执行一遍。
    resuming_llm = ScriptedLLM([LLMResponse(content="已完成", cost_cents=1)])
    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", resuming_llm.chat)
    run = await _load_and_execute(db_session, run_id, agent["id"])
    assert run.user_reply is None
    assert run.status == RunStatus.COMPLETED.value
    injected = [m["content"] for m in resuming_llm.calls[0][0] if m["role"] == "user"]
    assert injected[-1] == "客户名单：ABC 公司"
