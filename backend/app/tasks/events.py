"""Worker→API 事件封装（T069，US5）：publish_run_event 各事件类型的便捷封装。

对齐 contracts/api.md WS 事件类型：
status_change / step_new / step_update / log_line / cost_update /
run_completed / run_failed / run_cancelled / needs_input / run_timeout。

⚠️ 这里刻意用**同步** Redis 客户端，而不是 `app.core.events.publish_run_event`：
Worker 是 Celery prefork 的同步进程，原先每个事件都 `asyncio.run(...)`，
等于**每发一条事件就新建一个事件循环 + 一条 TCP 连接**。沙箱 stdout 按 4 KiB
分块上报，1 MB 输出就是 256 次建连，全在关键路径上同步等待。

同步客户端用一个进程级单例（连接池），线程安全的：沙箱读取线程直接调用即可，
不必再绕 `loop.call_soon_threadsafe`。
"""

import json
from typing import Any

import redis
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_run_event_prefix = "run:"

_client: redis.Redis | None = None


def channel_for_run(run_id: str) -> str:
    """频道名：必须与 API 侧订阅方（app.core.events）保持一致。"""
    return f"{_run_event_prefix}{run_id}"


def _redis_client() -> redis.Redis:
    """进程级单例（懒创建）。

    Celery prefork 下模块在父进程导入、客户端在子进程首次用到时才创建，
    因此每条连接天然属于 worker 自己，不存在跨进程复用 fd 的问题。
    超时必须有：Redis 抖动时要「快速失败」，不能把 Worker 挂在 publish 上。
    """
    global _client
    if _client is None:
        _client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
    return _client


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


def sandbox_started(
    run_id: str, execution_id: str, step_seq: int, language: str, limits: dict[str, Any]
) -> None:
    _publish(
        run_id,
        {
            "type": "sandbox_started",
            "execution_id": execution_id,
            "step_seq": step_seq,
            "language": language,
            "limits": limits,
        },
    )


def sandbox_output(run_id: str, execution_id: str, stream: str, chunk: str) -> None:
    data = chunk.encode("utf-8", errors="replace")
    offset = 0
    while offset < len(data):
        text = data[offset : offset + 4096].decode("utf-8", errors="ignore")
        offset += len(text.encode("utf-8"))
        _publish(
            run_id,
            {
                "type": "sandbox_output",
                "execution_id": execution_id,
                "stream": stream,
                "chunk": text,
            },
        )


def sandbox_finished(
    run_id: str,
    execution_id: str,
    status: str,
    exit_code: int | None,
    duration_ms: int,
    artifacts: int = 0,
) -> None:
    _publish(
        run_id,
        {
            "type": "sandbox_finished",
            "execution_id": execution_id,
            "status": status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "artifacts": artifacts,
        },
    )


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
            "reason": "出网策略拒绝该连接",
        },
    )


def _publish(run_id: str, payload: dict[str, Any]) -> None:
    """发布事件；Redis 抖动不抛出（Worker 不因事件失败中断执行）。"""
    try:
        _redis_client().publish(
            channel_for_run(run_id), json.dumps(payload, ensure_ascii=False)
        )
    except (redis.RedisError, OSError, ValueError) as exc:
        # 注意：redis.exceptions.ConnectionError **不继承** 内建 ConnectionError，
        # 必须显式捕获 redis.RedisError，否则 Redis 抖动会把 Worker 的整次执行打断。
        logger.warning(
            "event_publish_failed",
            run_id=run_id,
            event_type=payload.get("type"),
            error=type(exc).__name__,
        )
