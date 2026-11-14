"""ReAct 执行器（T068，US5）：LLM→tool_call→executor→回填→再 LLM 的消息序列循环。

对齐 tasks.md T068 与 R-04/R-05（FR-014/FR-015/FR-019/FR-021）：
- 循环直至 final / max_steps 超限 / 超时 / 成本超限
- 每步写 steps 表（save_step 回调）+ 发布事件（publish_event 回调）+ 累计 cost_cents
- needs_input 时挂起（抛 NeedsInput，run 置 needs_input 等待 reply）
- LLM 异常 → 转为可读错误（run 置 failed，正常返回）

持久化通过回调注入：runner 不依赖 DB 会话（可单元测试 / 可被 Celery 任务适配）。
run 对象满足 state_machine.RunLike 协议（ORM Run 或测试 dataclass）。
"""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from app.orchestrator.state_machine import RunLike, RunStatus, transition


class MaxStepsExceeded(Exception):
    """超过最大步骤数：执行终止。"""


class ExecutionTimeout(Exception):
    """运行超时：执行终止。"""


class CostLimitExceeded(Exception):
    """成本超限（熔断）：执行终止（FR-019/SC-006）。"""


class NeedsInput(Exception):
    """需要补充信息：挂起等待用户回复（FR-021）。"""


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
    ) -> None:
        self.llm = llm
        self.executors = executors
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds
        self.cost_limit_cents = cost_limit_cents
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
    ) -> str:
        """执行一次 ReAct 循环，返回最终状态字符串。

        Raises:
            MaxStepsExceeded / ExecutionTimeout / CostLimitExceeded / NeedsInput:
                控制流终止（run 状态已设置，异常上抛由调用方捕获）
        """
        # 进入 running
        transition(run, RunStatus.RUNNING)
        await set_status(run.status)
        await publish_event({"type": "status_change", "status": run.status})

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": request_text},
        ]

        step_count = 0
        seq = 0  # 全局单调步骤序号（LLM/tool/final 共用，避免跨轮重复）
        run_cost = 0
        start = time.monotonic()
        tools = [executor.schema() for executor in self.executors.values()]

        while True:
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
            except Exception as exc:
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
                "input": messages[-1],
                "output": {"content": resp.content, "tool_calls": _jsonable(resp.tool_calls)},
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
                    if executor is None:
                        output: dict[str, Any] = {
                            "error": f"未知工具 {tc.name}：执行器未注册"
                        }
                        tool_name = tc.name
                    else:
                        try:
                            result = await executor.execute(tc.arguments)
                            output = _jsonable(result)
                        except Exception as exc:
                            output = {"error": f"工具 {tc.name} 执行失败: {exc}"}
                        tool_name = tc.name

                    tool_duration_ms = int((time.monotonic() - tool_started) * 1000)
                    tool_step: dict[str, Any] = {
                        "seq": seq,
                        "kind": "tool",
                        "status": "completed",
                        "input": tc.arguments,
                        "output": output,
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
                    await publish_event({"type": "step_update", "step": tool_step})

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
