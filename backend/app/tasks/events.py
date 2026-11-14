"""Worker→API 事件封装（T069，US5）：publish_run_event 各事件类型的便捷封装。

对齐 contracts/api.md WS 事件类型：
status_change / step_new / step_update / log_line / cost_update /
run_completed / run_failed / run_cancelled / needs_input / run_timeout。
"""

from typing import Any

from app.core.events import publish_run_event


def status_change(run_id: str, status: str) -> None:
    _publish(run_id, {"type": "status_change", "status": status})


def step_new(run_id: str, step: dict[str, Any]) -> None:
    _publish(run_id, {"type": "step_new", "step": step})


def step_update(run_id: str, step: dict[str, Any]) -> None:
    _publish(run_id, {"type": "step_update", "step": step})


def log_line(run_id: str, line: str, level: str = "info") -> None:
    _publish(run_id, {"type": "log_line", "line": line, "level": level})


def cost_update(run_id: str, cost_cents: int) -> None:
    _publish(run_id, {"type": "cost_update", "cost_cents": cost_cents})


def run_completed(run_id: str, result: dict[str, Any] | None) -> None:
    _publish(run_id, {"type": "run_completed", "result": result})


def run_failed(run_id: str, error: str) -> None:
    _publish(run_id, {"type": "run_failed", "error": error})


def run_cancelled(run_id: str) -> None:
    _publish(run_id, {"type": "run_cancelled"})


def run_timeout(run_id: str) -> None:
    _publish(run_id, {"type": "run_timeout"})


def needs_input(run_id: str, message: str) -> None:
    _publish(run_id, {"type": "needs_input", "message": message})


def sandbox_started(run_id: str, execution_id: str, step_seq: int, language: str, limits: dict[str, Any]) -> None:
    _publish(run_id, {"type": "sandbox_started", "execution_id": execution_id, "step_seq": step_seq, "language": language, "limits": limits})


def sandbox_output(run_id: str, execution_id: str, stream: str, chunk: str) -> None:
    _publish(run_id, {"type": "sandbox_output", "execution_id": execution_id, "stream": stream, "chunk": chunk[:4096]})


def sandbox_finished(run_id: str, execution_id: str, status: str, exit_code: int | None, duration_ms: int, artifacts: int = 0) -> None:
    _publish(run_id, {"type": "sandbox_finished", "execution_id": execution_id, "status": status, "exit_code": exit_code, "duration_ms": duration_ms, "artifacts": artifacts})


def egress_denied(
    run_id: str,
    execution_id: str,
    *,
    target_ip: str,
    target_domain: str,
    port: int,
    matched_rule_id: str | None,
) -> None:
    _publish(
        run_id,
        {
            "type": "egress_denied",
            "execution_id": execution_id,
            "target_ip": target_ip,
            "target_domain": target_domain,
            "port": port,
            "matched_rule_id": matched_rule_id,
        },
    )


def _publish(run_id: str, payload: dict[str, Any]) -> None:
    """发布事件；Redis 抖动不抛出（Worker 不因事件失败中断执行）。"""
    try:
        # 异步发布：Worker 上下文无事件循环时退化为同步 publish
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(publish_run_event(run_id, payload))
        else:
            asyncio.ensure_future(publish_run_event(run_id, payload))
    except (ConnectionError, OSError, ValueError) as exc:
        import structlog

        structlog.get_logger(__name__).warning("event_publish_failed", run_id=run_id, error=str(exc))
