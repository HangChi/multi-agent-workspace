"""ReAct 执行器（T068，US5）：LLM→tool_call→executor→回填→再 LLM 的消息序列循环。

对齐 tasks.md T068 与 R-04/R-05（FR-014/FR-015/FR-019/FR-021）：
- 循环直至 final / max_steps 超限 / 超时 / 成本超限
- 每步写 steps 表（save_step 回调）+ 发布事件（publish_event 回调）+ 累计 cost_cents
- needs_input 时挂起（抛 NeedsInput，run 置 needs_input 等待 reply）
- LLM 异常 → 转为可读错误（run 置 failed，正常返回）

持久化通过回调注入：runner 不依赖 DB 会话（可单元测试 / 可被 Celery 任务适配）。
run 对象满足 state_machine.RunLike 协议（ORM Run 或测试 dataclass）。
"""

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from app.core.config import settings
from app.executors.base import DeniedToolResult
from app.executors.catalog import CATALOG
from app.orchestrator.state_machine import RunLike, RunStatus, transition


class MaxStepsExceeded(Exception):
    """超过最大步骤数：执行终止。"""


class ExecutionTimeout(Exception):
    """运行超时：执行终止。"""


class CostLimitExceeded(Exception):
    """成本超限（熔断）：执行终止（FR-019/SC-006）。"""


class NeedsInput(Exception):
    """需要补充信息：挂起等待用户回复（FR-021）。"""


def _redact_tool_calls(value: Any) -> Any:
    """Redact sandbox source code from persisted LLM step payloads."""
    calls = _jsonable(value)
    if not isinstance(calls, list):
        return calls
    for call in calls:
        if not isinstance(call, dict) or call.get("name") not in {"run_python", "run_shell"}:
            continue
        call["arguments"] = {"source": "[stored in sandbox execution detail]"}
    return calls


def _step_input(message: Any) -> Any:
    """Keep verbose sandbox tool results out of generic step rows."""
    if not isinstance(message, dict):
        return message
    sanitized = dict(message)
    if sanitized.get("role") == "tool" and sanitized.get("name") in {"run_python", "run_shell"}:
        sanitized["content"] = "[sandbox output is available in execution detail]"
    if sanitized.get("role") == "assistant" and isinstance(sanitized.get("tool_calls"), list):
        sanitized["tool_calls"] = _redact_tool_calls(sanitized["tool_calls"])
    return sanitized


def _jsonable(value: Any) -> Any:
    """把执行器返回值规范化为可 JSON 序列化结构。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if is_dataclass(value):
        return _jsonable(asdict(value) if not isinstance(value, type) else {})
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


StepSink = Callable[[dict[str, Any]], Awaitable[Any]]
EventSink = Callable[[dict[str, Any]], Awaitable[Any]]


class ReActRunner:
    """消息序列循环执行器：一次 Run 的完整 ReAct 循环。"""

    def __init__(
        self,
        *,
        llm: Any,
        executors: dict[str, Any],
        max_steps: int = 10,
        timeout_seconds: int = 1800,
        cost_limit_cents: int = 10000,
        system_prompt: str | None = None,
        audit_denied: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
        on_sandbox_started: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
        on_sandbox_result: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> None:
        self.llm = llm
        self.executors = executors
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds
        self.cost_limit_cents = cost_limit_cents
        self.audit_denied = audit_denied
        self.on_sandbox_started = on_sandbox_started
        self.on_sandbox_result = on_sandbox_result
        self.system_prompt = system_prompt or (
            "你是一个任务执行助手。你需要：1) 理解用户需求；2) 必要时调用工具获取信息；"
            "3) 最终用『最终答案』格式给出结果。如果缺少关键信息，回复 __NEEDS_INPUT__ 加补充要求。"
        )

    async def run(
        self,
        *,
        run: RunLike,
        request_text: str,
        save_step: StepSink,
        publish_event: EventSink,
        set_status: Callable[[str], Awaitable[Any]],
        set_cost: Callable[[int], Awaitable[Any]],
        extra_user_messages: list[str] | None = None,
        should_abort: Callable[[], Awaitable[bool]] | None = None,
    ) -> str:
        """执行一次 ReAct 循环，返回最终状态字符串。

        Args:
            extra_user_messages: needs_input 恢复执行时注入的用户回复（FR-021）。
            should_abort: 外部取消检查回调（FR-018）：每轮循环前 await，True 则终止执行。

        Raises:
            MaxStepsExceeded / ExecutionTimeout / CostLimitExceeded / NeedsInput:
                控制流终止（run 状态已设置，异常上抛由调用方捕获）
        """
        run_id = str(getattr(run, "id", ""))
        # 进入 running
        transition(run, RunStatus.RUNNING)
        await set_status(run.status)
        await publish_event({"type": "status_change", "status": run.status})

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": request_text},
        ]
        # FR-021：needs_input 恢复时注入用户回复（追加到初始需求之后）
        for reply in extra_user_messages or []:
            messages.append({"role": "user", "content": reply})

        step_count = 0
        seq = 0  # 全局单调步骤序号（LLM/tool/final 共用，避免跨轮重复）
        denied_count = 0
        run_cost = 0
        start = time.monotonic()
        tools = [executor.schema() for executor in self.executors.values()]

        while True:
            # 0. 取消检查（FR-018）：外部已取消则终止，不继续执行
            if should_abort is not None and await should_abort():
                transition(run, RunStatus.CANCELLED)
                await set_status(run.status)
                await publish_event({"type": "status_change", "status": run.status})
                return "cancelled"

            # 1. 超时检查（进入下一轮前）
            if time.monotonic() - start > self.timeout_seconds:
                transition(run, RunStatus.TIMEOUT)
                await set_status(run.status)
                await publish_event({"type": "run_timeout"})
                raise ExecutionTimeout(f"运行超过 {self.timeout_seconds}s 上限")

            # 2. max_steps 检查
            if step_count >= self.max_steps:
                transition(run, RunStatus.FAILED)
                run.error = f"已超过最大步骤数（{self.max_steps}），执行终止"
                await set_status(run.status)
                await publish_event({"type": "run_failed", "error": run.error})
                raise MaxStepsExceeded(run.error)

            # 3. LLM 调用
            step_count += 1
            seq += 1
            started = time.monotonic()
            try:
                resp = await self.llm.chat(messages, tools=tools or None)
            except Exception as exc:  # noqa: BLE001 - LLM 兜底：任何异常转可读错误（FR-017）
                # LLM 异常 → 可读错误（FR-017），正常返回
                transition(run, RunStatus.FAILED)
                run.error = f"LLM 调用失败: {exc}"
                await set_status(run.status)
                await publish_event({"type": "run_failed", "error": run.error})
                return run.status

            duration_ms = int((time.monotonic() - started) * 1000)

            # 4. 累计成本 + 熔断检查（FR-019/SC-006：cost > limit 终止）
            run_cost += resp.cost_cents
            run.cost_cents = run_cost
            await set_cost(run_cost)
            await publish_event({"type": "cost_update", "cost_cents": run_cost})
            if run_cost > self.cost_limit_cents:
                transition(run, RunStatus.FAILED)
                run.error = f"成本超限（{run_cost} 分 > 上限 {self.cost_limit_cents} 分），已熔断终止"
                await set_status(run.status)
                await publish_event({"type": "run_failed", "error": run.error})
                raise CostLimitExceeded(run.error)

            # 5. 写 LLM 步骤 + 发布事件
            llm_step: dict[str, Any] = {
                "seq": seq,
                "kind": "llm",
                "status": "completed",
                "input": _step_input(messages[-1]),
                "output": {"content": resp.content, "tool_calls": _redact_tool_calls(resp.tool_calls)},
                "cost_cents": resp.cost_cents,
                "duration_ms": duration_ms,
            }
            await publish_event(
                {
                    "type": "step_new",
                    "step": {"seq": seq, "kind": "llm", "status": "running"},
                }
            )
            await save_step(llm_step)
            await publish_event({"type": "step_update", "step": llm_step})

            # 6. needs_input 挂起（FR-021）
            if resp.needs_input:
                transition(run, RunStatus.NEEDS_INPUT)
                message = resp.needs_input_message or "需要补充信息"
                await set_status(run.status)
                await publish_event({"type": "needs_input", "message": message})
                raise NeedsInput(message)

            # 7. tool_call → 执行 → 回填
            if resp.tool_calls:
                for tc in resp.tool_calls:
                    seq += 1
                    tool_started = time.monotonic()
                    executor = self.executors.get(tc.name)
                    sandbox_payload: dict[str, Any] | None = None
                    sandbox_step_id: str | None = None
                    denied = executor is None and tc.name in CATALOG
                    if executor is None:
                        if denied:
                            denied_count += 1
                            output = DeniedToolResult.build(tc.name, sorted(self.executors))
                        else:
                            # 完全未知的工具名不是「越权」，保留旧的可读错误语义。
                            output = {"error": f"未知工具 {tc.name}：执行器未注册"}
                        tool_name = tc.name
                        if denied:
                            await publish_event(
                                {
                                    "type": "tool_denied",
                                    "tool": tc.name,
                                    "message": output["message"],
                                }
                            )
                            if self.audit_denied is not None:
                                await self.audit_denied(
                                    {
                                        "tool_name": tc.name,
                                        "available_tools": sorted(self.executors),
                                    }
                                )
                    else:
                        # 沙箱执行在工作线程内轮询取消状态。通过线程安全桥接回当前
                        # asyncio loop，确保运行中的容器也能响应 Run 取消，而非仅在
                        # 下一轮 LLM 请求前取消。
                        set_abort_check = getattr(executor, "set_abort_check", None)
                        if callable(set_abort_check) and should_abort is not None:
                            event_loop = asyncio.get_running_loop()

                            async def cancellation_probe() -> bool:
                                return await should_abort()

                            def abort_from_worker(loop=event_loop) -> bool:
                                try:
                                    return bool(
                                        asyncio.run_coroutine_threadsafe(
                                            cancellation_probe(), loop
                                        ).result(timeout=2)
                                    )
                                except (RuntimeError, TimeoutError):
                                    # 取消状态检查失败时采取 fail-closed，避免孤儿容器。
                                    return True

                            set_abort_check(abort_from_worker)
                        set_timeout_cap = getattr(executor, "set_timeout_cap", None)
                        if callable(set_timeout_cap):
                            remaining = max(
                                0, int(self.timeout_seconds - (time.monotonic() - start))
                            )
                            set_timeout_cap(remaining)
                        prepare_execution = getattr(executor, "prepare_execution", None)
                        if callable(prepare_execution):
                            try:
                                prepared = prepare_execution(tc.arguments)
                            except TypeError:
                                prepared = prepare_execution()
                            if isinstance(prepared, tuple) and len(prepared) == 2:
                                execution_id, limits = prepared
                                sandbox_step_id = str(uuid.uuid4())
                                if self.on_sandbox_started is not None:
                                    await self.on_sandbox_started(
                                        {
                                            "run_id": run_id,
                                            "execution_id": execution_id,
                                            "step_seq": seq,
                                            "step_id": sandbox_step_id,
                                            "language": getattr(executor, "language", "python"),
                                            "limits": limits,
                                            "code": tc.arguments.get(
                                                getattr(executor, "argument_name", "code"), ""
                                            ),
                                            "tool_name": tc.name,
                                        }
                                    )
                        try:
                            result = await executor.execute(tc.arguments)
                            if isinstance(result, dict):
                                raw_payload = result.pop("_sandbox_payload", None)
                                if isinstance(raw_payload, dict):
                                    sandbox_payload = raw_payload
                                    sandbox_payload["artifacts"] = result.pop(
                                        "_sandbox_artifacts", sandbox_payload.get("artifacts", [])
                                    )
                                    sandbox_payload["artifact_truncated"] = result.pop(
                                        "_sandbox_artifact_truncated",
                                        sandbox_payload.get("artifact_truncated"),
                                    )
                                    sandbox_payload["egress_accesses"] = result.pop(
                                        "_sandbox_egress_accesses",
                                        sandbox_payload.get("egress_accesses", []),
                                    )
                                    sandbox_payload["policy_denied"] = bool(
                                        result.pop(
                                            "_sandbox_policy_denied",
                                            sandbox_payload.get("policy_denied", False),
                                        )
                                    )
                            output = _jsonable(result)
                            if sandbox_payload is not None:
                                sandbox_payload["_result"] = output
                        except Exception as exc:  # noqa: BLE001 - 工具异常转为可读 output.error
                            output = {"error": f"工具 {tc.name} 执行失败: {exc}"}
                        tool_name = tc.name

                    tool_duration_ms = int((time.monotonic() - tool_started) * 1000)
                    sandbox_step_summary = {
                        key: output[key]
                        for key in (
                            "ok", "status", "error_category", "exit_code", "message",
                            "duration_ms", "stdout_truncated", "stdout_full_bytes",
                            "execution_id", "artifact_truncated", "policy_denied",
                        )
                        if sandbox_payload is not None and key in output
                    }
                    tool_step: dict[str, Any] = {
                        "id": sandbox_step_id or (str(uuid.uuid4()) if sandbox_payload is not None else None),
                        "seq": seq,
                        "kind": "tool",
                        "status": "completed",
                        "input": (
                            {"execution_id": sandbox_payload["execution_id"]}
                            if sandbox_payload is not None
                            else tc.arguments
                        ),
                        "output": sandbox_step_summary if sandbox_payload is not None else output,
                        "tool_name": tool_name,
                        "cost_cents": 0,
                        "duration_ms": tool_duration_ms,
                    }
                    await publish_event(
                        {
                            "type": "step_new",
                            "step": {"seq": seq, "kind": "tool", "status": "running"},
                        }
                    )
                    await save_step(tool_step)
                    if sandbox_payload is not None and self.on_sandbox_result is not None:
                        await self.on_sandbox_result(
                            {
                                "run_id": run_id,
                                "step_seq": seq,
                                "tool_name": tc.name,
                                "step_id": tool_step["id"],
                                "step": tool_step,
                                "payload": sandbox_payload,
                                "result": sandbox_payload.get("_result", output),
                                "duration_ms": tool_duration_ms,
                            }
                        )
                    await publish_event({"type": "step_update", "step": tool_step})

                    if not denied:
                        denied_count = 0
                    if denied and denied_count >= settings.tool_deny_threshold:
                        transition(run, RunStatus.FAILED)
                        run.error = (
                            f"工具越权调用连续达到 {settings.tool_deny_threshold} 次，"
                            "为安全起见已中止当前运行"
                        )
                        await set_status(run.status)
                        await publish_event({"type": "run_failed", "error": run.error})
                        return run.status

                    # 回填给 LLM（assistant tool_call + tool 结果）
                    messages.append(
                        {
                            "role": "assistant",
                            "content": resp.content or "",
                            "tool_calls": [
                                {
                                    "id": f"call_{seq}",
                                    "type": "function",
                                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                                }
                            ],
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": f"call_{seq}",
                            "name": tc.name,
                            "content": json.dumps(output, ensure_ascii=False),
                        }
                    )
                continue

            # 8. final：产出最终结果
            seq += 1
            final_step: dict[str, Any] = {
                "seq": seq,
                "kind": "final",
                "status": "completed",
                "input": None,
                "output": {"answer": resp.content},
                "cost_cents": 0,
                "duration_ms": 0,
            }
            await publish_event(
                {"type": "step_new", "step": {"seq": seq, "kind": "final", "status": "running"}}
            )
            await save_step(final_step)
            await publish_event({"type": "step_update", "step": final_step})

            transition(run, RunStatus.COMPLETED)
            run.result = {"answer": resp.content}
            await set_status(run.status)
            await publish_event({"type": "run_completed", "result": run.result})
            return run.status
