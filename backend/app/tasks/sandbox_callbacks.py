"""沙箱执行的生命周期回调（R4：从 `tasks/execute_run.py` 的 `_execute` 搬出）。

这两个回调原先作为 `_execute` 的内嵌闭包存在，导致那个函数有 421 行、混了
七件不相干的事。搬出来只做了一件事——把闭包捕获的自由变量改成显式参数，
行为逐行保持不变：

| 闭包捕获 | 改成参数 |
|---|---|
| `db` / `run` / `agent` | 显式参数 |
| `runner`（只在 started 里用 `.executors`） | `executors` 映射（不传整个 runner，避免反向依赖） |

**为什么单独成文件而不是放回 `execute_run.py` 顶层**：`execute_run.py` 是
Celery 任务入口，导入它会拉起 `celery_app`；回调本身只依赖 service 层，
单测直接 import 本模块即可，不必拖起任务基建。

回调的语义边界（改动时别越界）：

- `publish_sandbox_started`：**开始**时落 step 行 + 沙箱执行行 + 发
  `sandbox_started`。它必须早于真实执行，否则前端拿不到 execution_id 去订阅
  输出流。
- `persist_sandbox_result`：**结束**时写沙箱事实、制品、egress 审计，最后发
  `sandbox_finished`。注意这是**步骤级**事件——沙箱失败只让这一步失败，Run
  仍可继续（与外部工具失败直接终结 Run 不在同一层，别对齐）。
"""

from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.models.agent import Agent
from app.models.run import Run
from app.models.step import Step
from app.services import egress_service, sandbox_service
from app.tasks import events as run_events


async def persist_sandbox_result(
    db: AsyncSession,
    run: Run,
    agent: Agent | None,
    detail: dict,
) -> None:
    """执行完成时写入沙箱事实、制品和审计，并发布运行详情事件。"""
    if agent is None:
        raise RuntimeError("沙箱执行缺少 Agent 归属")
    step = detail["step"]
    step_id = str(detail["step_id"])
    persisted_step = await db.get(Step, step_id)
    if persisted_step is None:
        raise RuntimeError(f"沙箱步骤 {step_id} 未初始化")
    persisted_step.output = step.get("output")
    persisted_step.duration_ms = step.get("duration_ms", 0)
    persisted_step.status = (
        "failed" if not detail["result"].get("ok", False) else "completed"
    )
    await db.flush()
    payload = detail["payload"]
    result = detail["result"]
    execution = await sandbox_service.persist_sandbox_result(
        db,
        run_id=run.id,
        step_id=step_id,
        agent_id=agent.id,
        user_id=run.owner_id,
        payload=payload,
        result=result,
    )
    egress_records = await egress_service.record_accesses(
        db,
        run_id=run.id,
        execution_id=execution.id,
        agent_id=agent.id,
        user_id=run.owner_id,
        accesses=list(payload.get("egress_accesses") or []),
    )
    for access in egress_records:
        if access.action != "deny":
            continue
        access_detail: dict[str, Any] = {
            "execution_id": execution.id,
            "target_ip": str(access.target_ip),
            "target_domain": access.target_domain or "ip-direct",
            "port": access.port,
            "matched_rule_id": access.matched_rule_id,
        }
        await write_audit(
            db,
            user_id=run.owner_id,
            action="egress_denied",
            target_type="sandbox_execution",
            target_id=execution.id,
            detail=access_detail,
        )
        run_events.egress_denied(
            run.id,
            execution.id,
            target_ip=access_detail["target_ip"],
            target_domain=access_detail["target_domain"],
            port=access_detail["port"],
            matched_rule_id=access_detail["matched_rule_id"],
        )
    await db.commit()
    run_events.sandbox_finished(
        run.id,
        execution.id,
        execution.status,
        execution.exit_code,
        execution.duration_ms,
        artifacts=len(payload.get("artifacts") or []),
    )


async def publish_sandbox_started(
    db: AsyncSession,
    run: Run,
    agent: Agent | None,
    executors: dict[str, Any],
    detail: dict,
) -> None:
    """沙箱开始前的准备：挂输出回调、快照 egress 规则、落行、发事件。"""
    if agent is None:
        raise RuntimeError("沙箱执行缺少 Agent 归属")
    executor = executors.get(str(detail["tool_name"]))
    set_output_callback = getattr(executor, "set_output_callback", None)
    if callable(set_output_callback):
        run_id, execution_id = str(run.id), str(detail["execution_id"])

        def publish_output(stream: str, text: str) -> None:
            # 事件发布已是同步单例客户端（线程安全），沙箱读取线程可直接调用：
            # 不再经 loop.call_soon_threadsafe 绕一圈，也就不会阻塞事件循环。
            run_events.sandbox_output(run_id, execution_id, stream, text)

        set_output_callback(publish_output)
    set_egress_snapshot = getattr(executor, "set_egress_snapshot", None)
    if callable(set_egress_snapshot):
        (
            mode,
            global_rules,
            agent_rules,
        ) = await egress_service.snapshot_for_execution(db, agent_id=agent.id)
        set_egress_snapshot(
            {
                "mode": mode,
                "global_rules": [asdict(rule) for rule in global_rules],
                "agent_rules": [asdict(rule) for rule in agent_rules],
            }
        )
    step_id = str(detail["step_id"])
    db.add(
        Step(
            id=step_id,
            run_id=run.id,
            seq=int(detail["step_seq"]),
            kind="tool",
            input={"tool_name": detail["tool_name"]},
            output=None,
            tool_name=str(detail["tool_name"]),
            status="running",
        )
    )
    await db.flush()
    await sandbox_service.start_execution(
        db,
        run_id=run.id,
        step_id=step_id,
        agent_id=agent.id,
        language=str(detail["language"]),
        code=str(detail["code"]),
        limits=dict(detail["limits"]),
        execution_id=str(detail["execution_id"]),
    )
    await db.commit()
    run_events.sandbox_started(
        run.id,
        str(detail["execution_id"]),
        int(detail["step_seq"]),
        str(detail["language"]),
        dict(detail["limits"]),
    )
