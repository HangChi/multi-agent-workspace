"""沙箱执行结果的统一分类与持久化辅助。"""

from __future__ import annotations

import mimetypes
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.audit import write_audit
from app.core.config import settings
from app.executors.sandbox_client import OutputFile, SandboxOutputLimit, SandboxRun
from app.models.artifact import Artifact
from app.models.sandbox_execution import SandboxExecution
from app.services import artifact_service

ERROR_MESSAGES = {
    "timeout": "脚本执行超时，已终止。请缩短计算量或增大 timeout_s。",
    "cancelled": "脚本执行已取消。",
    "oom": "脚本因内存不足被终止，请减少内存占用。",
    "pids": "脚本创建的进程数超过限制，请减少并发子进程。",
    "disk": "脚本写入内容超过磁盘限额，请减少临时文件或产物大小。",
    "infra": "沙箱基础设施不可用，请稍后重试。",
    "exit_code": "脚本以非零退出码结束，请检查 stderr 输出。",
}


def finish_payload(result: dict[str, Any]) -> dict[str, Any]:
    """将执行器结果规范化为 step/API 可直接使用的结构。"""
    payload = dict(result)
    category = payload.get("error_category")
    if category and not payload.get("message"):
        payload["message"] = ERROR_MESSAGES.get(category, "沙箱执行失败。")
    payload.setdefault("finished_at", datetime.now(UTC).isoformat())
    return payload


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def start_execution(
    db: AsyncSession,
    *,
    run_id: str,
    step_id: str,
    agent_id: str,
    language: str,
    code: str,
    limits: dict[str, Any],
) -> SandboxExecution:
    """落一条 running 执行记录，并保存代码指纹。"""
    execution = SandboxExecution(
        run_id=run_id,
        step_id=step_id,
        agent_id=agent_id,
        language=language,
        code=code,
        code_sha256=_sha256_text(code),
        image=settings.sbx_image,
        limits=limits,
        status="running",
    )
    db.add(execution)
    await db.flush()
    return execution


async def finish_execution(
    db: AsyncSession,
    execution: SandboxExecution,
    result: dict[str, Any],
    *,
    user_id: str | None = None,
) -> SandboxExecution:
    """把执行器统一结果写回 DB，失败分类与文案原样保留。"""
    payload = finish_payload(result)
    execution.status = str(payload.get("status", "failed"))
    execution.exit_code = payload.get("exit_code")
    execution.stdout = payload.get("stdout")
    execution.stderr = payload.get("stderr")
    execution.stdout_truncated = bool(payload.get("stdout_truncated", False))
    execution.stdout_full_bytes = int(payload.get("stdout_full_bytes", 0))
    execution.duration_ms = int(payload.get("duration_ms", 0))
    execution.cpu_ms = payload.get("cpu_ms")
    execution.mem_peak_bytes = payload.get("mem_peak_bytes")
    execution.error_category = payload.get("error_category")
    execution.error_message = payload.get("message")
    execution.finished_at = datetime.now(UTC)
    await db.flush()
    if user_id:
        await write_audit(
            db,
            user_id=user_id,
            action="sandbox_exec",
            target_type="sandbox_execution",
            target_id=execution.id,
            detail={
                "language": execution.language,
                "status": execution.status,
                "exit_code": execution.exit_code,
                "duration_ms": execution.duration_ms,
            },
        )
    return execution


def _unique_filename(name: str, used: set[str]) -> str:
    """同一执行内同名制品追加 -2/-3，避免覆盖对象。"""
    if name not in used:
        return name
    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    for index in range(2, 10_000):
        candidate = f"{stem}-{index}.{suffix}" if suffix else f"{stem}-{index}"
        if candidate not in used:
            return candidate
    raise AppError("INVALID_INPUT", "制品重名数量超过上限")


async def collect_artifacts(
    db: AsyncSession,
    sandbox: SandboxRun,
    *,
    run_id: str,
    execution_id: str,
    step_id: str | None = None,
    user_id: str | None = None,
) -> list[Artifact]:
    """从 output/ 流式回收制品并写入 MinIO 与 artifacts 表。"""
    artifacts: list[Artifact] = []
    used: set[str] = set()
    try:
        files: Iterable[OutputFile] = sandbox.iter_output_files(
            max_files=settings.sbx_artifact_max_files,
            max_file_bytes=settings.sbx_artifact_max_file_mb * 1024 * 1024,
            max_total_bytes=settings.sbx_artifact_max_total_mb * 1024 * 1024,
        )
        for item in files:
            filename = _unique_filename(item.name, used)
            used.add(filename)
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            object_key, sha256 = artifact_service.put_bytes(
                run_id, execution_id, filename, item.data, mime=mime
            )
            artifact = Artifact(
                run_id=run_id,
                step_id=step_id,
                object_key=object_key,
                filename=filename,
                size_bytes=item.size,
                mime_type=mime,
                sha256=sha256,
            )
            db.add(artifact)
            artifacts.append(artifact)
        await db.flush()
    except SandboxOutputLimit as exc:
        if user_id:
            await write_audit(
                db,
                user_id=user_id,
                action="artifact_truncated",
                target_type="sandbox_execution",
                target_id=execution_id,
                detail={"reason": exc.reason, "message": str(exc)},
            )
    return artifacts
