"""隔离容器代码执行器。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import redis

from app.core.config import settings
from app.executors.base import ToolExecutor
from app.executors.catalog import CATALOG, ToolSpec
from app.executors.sandbox_client import SandboxInfraError, SandboxRun

logger = logging.getLogger(__name__)

_ERROR_MESSAGES = {
    "timeout": "脚本执行超时，已终止。请缩短计算量或增大 timeout_s。",
    "cancelled": "脚本执行已取消。",
    "oom": "脚本因内存不足被终止，请减少内存占用。",
    "pids": "脚本创建的进程数超过限制，请减少并发子进程。",
    "disk": "脚本写入内容超过磁盘限额，请减少临时文件或产物大小。",
    "infra": "沙箱基础设施不可用，请稍后重试。",
    "exit_code": "脚本以非零退出码结束，请检查 stderr 输出。",
}


def decode_output(data: bytes) -> str:
    """将容器输出安全解码为可展示文本。"""
    return data.decode("utf-8", errors="replace")


def classify_error(error: BaseException) -> tuple[str, str]:
    """将边界异常归类为稳定的 API 状态和错误分类。"""
    text = str(error).lower()
    if "oom" in text or "out of memory" in text:
        return "resource", "oom"
    if "pids" in text or "process limit" in text:
        return "resource", "pids"
    if "disk" in text or "no space" in text or "enospc" in text:
        return "resource", "disk"
    if "timeout" in text or "timed out" in text:
        return "timeout", "timeout"
    return "infra", "infra"


def classify_outcome(outcome: Any) -> tuple[str, str | None]:
    if outcome.cancelled:
        return "failed", "cancelled"
    if outcome.timed_out:
        return "timeout", "timeout"
    if outcome.oom_killed:
        return "resource", "oom"
    if outcome.exit_code is not None and outcome.exit_code != 0:
        # 137 只能在 daemon OOM 标记缺失时作为资源兜底；被执行器 kill 的超时已提前返回。
        if outcome.exit_code == 137:
            return "resource", "oom"
        return "failed", "exit_code"
    return "ok", None


def _message(category: str | None, outcome: Any) -> str | None:
    if category is None:
        return None
    text = _ERROR_MESSAGES[category]
    if category == "exit_code":
        text = f"{text}（退出码 {outcome.exit_code}）"
    return text


class SandboxExecutor(ToolExecutor):
    language: str
    argument_name: str

    def __init__(
        self,
        *,
        execution_id: str | None = None,
        client: Any | None = None,
        abort_check: Any | None = None,
        timeout_cap: int | None = None,
    ) -> None:
        self.execution_id = execution_id or str(uuid.uuid4())
        self.client = client
        self.abort_check = abort_check
        self.timeout_cap = timeout_cap
        self.last_execution_id = self.execution_id

    def set_abort_check(self, abort_check: Any | None) -> None:
        """更新执行中的取消探针（由 ReAct runner 在工具调用前注入）。"""
        self.abort_check = abort_check

    def set_timeout_cap(self, timeout_cap: int | None) -> None:
        """把单次工具超时限制在 Run 剩余预算内。"""
        self.timeout_cap = timeout_cap

    def prepare_execution(self) -> tuple[str, dict[str, Any]]:
        """为每次工具调用生成一次性执行 ID 和限额快照。"""
        self.execution_id = str(uuid.uuid4())
        self.last_execution_id = self.execution_id
        return self.execution_id, {
            "timeout_s": self.timeout_cap or settings.sbx_timeout_default,
            "cpu": settings.sbx_cpu,
            "mem_mb": settings.sbx_mem_mb,
            "disk_mb": settings.sbx_disk_mb,
            "pids": settings.sbx_pids,
        }

    @property
    def spec(self) -> ToolSpec:
        return CATALOG[self.name]

    def schema(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.param_schema,
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raw = arguments.get(self.argument_name)
        if not isinstance(raw, str) or not raw.strip():
            return {
                "ok": False,
                "status": "failed",
                "error_category": "exit_code",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "message": f"缺少 {self.argument_name} 参数。",
            }
        requested_timeout = arguments.get("timeout_s")
        timeout_s = requested_timeout if isinstance(requested_timeout, int) else None
        if self.timeout_cap is not None:
            timeout_s = min(timeout_s or self.timeout_cap, self.timeout_cap)
        from app.services.sandbox_service import (
            acquire_concurrency_slot,
            release_concurrency_slot,
        )

        try:
            slot = await asyncio.to_thread(acquire_concurrency_slot)
        except Exception as exc:  # noqa: BLE001 - Redis 不可用时视为基础设施故障
            return {
                "ok": False,
                "status": "infra",
                "error_category": "infra",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "message": f"{_ERROR_MESSAGES['infra']}（无法获取并发槽位：{exc}）",
            }
        if slot is None:
            return {
                "ok": False,
                "status": "resource",
                "error_category": "resource_busy",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "message": "沙箱并发已满，请稍后重试。",
            }
        try:
            return await asyncio.to_thread(self._execute_sync, raw, timeout_s)
        except Exception as exc:  # noqa: BLE001 - 工具边界必须返回结构化失败
            status, category = classify_error(exc)
            return {
                "ok": False,
                "status": status,
                "error_category": category,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "message": f"{_ERROR_MESSAGES.get(category, _ERROR_MESSAGES['infra'])}（{exc}）",
            }
        finally:
            try:
                await asyncio.to_thread(release_concurrency_slot, slot)
            except (redis.RedisError, OSError) as exc:
                # 释放失败由 TTL 自愈，不能覆盖原始执行结果
                logger.warning("sandbox_slot_release_failed: %s", exc)
    def _execute_sync(self, code: str, timeout_s: int | None) -> dict[str, Any]:
        if len(code.encode("utf-8")) > settings.sbx_code_max_bytes:
            return {
                "ok": False,
                "status": "resource",
                "error_category": "disk",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "message": "脚本内容超过代码大小限制。",
            }
        run = SandboxRun(
            execution_id=self.execution_id,
            language=self.language,  # type: ignore[arg-type]
            code=code,
            timeout_s=timeout_s,
            abort_check=self.abort_check,
            client=self.client,
        )
        try:
            with run:
                outcome = run.execute()
                status, category = classify_outcome(outcome)
                result: dict[str, Any] = {
                    "ok": status == "ok",
                    "status": status,
                    "error_category": category,
                    "exit_code": outcome.exit_code,
                    "stdout": decode_output(outcome.stdout),
                    "stderr": decode_output(outcome.stderr),
                    "stdout_truncated": outcome.stdout_truncated,
                    "stdout_full_bytes": outcome.stdout_full_bytes,
                    "stderr_truncated": outcome.stderr_truncated,
                    "stderr_full_bytes": outcome.stderr_full_bytes,
                    "duration_ms": outcome.duration_ms,
                    "cpu_ms": outcome.cpu_ms,
                    "mem_peak_bytes": outcome.mem_peak_bytes,
                    "limits": run.limits.as_dict(),
                    "message": _message(category, outcome),
                }
                return result
        except SandboxInfraError as exc:
            return {
                "ok": False,
                "status": "infra",
                "error_category": "infra",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "message": f"{_ERROR_MESSAGES['infra']}（{exc}）",
            }


class RunPythonExecutor(SandboxExecutor):
    name = "run_python"
    language = "python"
    argument_name = "code"


class RunShellExecutor(SandboxExecutor):
    name = "run_shell"
    language = "shell"
    argument_name = "command"

    def _execute_sync(self, code: str, timeout_s: int | None) -> dict[str, Any]:
        result = super()._execute_sync(code, timeout_s)
        result["command_summary"] = code[:4096]
        return result


def sandbox_executors(**kwargs: Any) -> dict[str, ToolExecutor]:
    """构造沙箱执行器，供后续按 agent.tools 注入。"""
    return {
        "run_python": RunPythonExecutor(**kwargs),
        "run_shell": RunShellExecutor(**kwargs),
    }


def build_executors(names: list[str] | None = None, **kwargs: Any) -> dict[str, ToolExecutor]:
    """按 Agent 授权名构造执行器；未传 names 时保留旧的全量行为。"""
    from app.executors.http import builtin_tools

    available: dict[str, ToolExecutor] = {executor.name: executor for executor in builtin_tools()}
    available.update(sandbox_executors(**kwargs))
    if names is None:
        return available
    return {name: available[name] for name in names if name in available}


__all__ = [
    "RunPythonExecutor",
    "RunShellExecutor",
    "SandboxExecutor",
    "classify_error",
    "classify_outcome",
    "decode_output",
    "sandbox_executors",
]
