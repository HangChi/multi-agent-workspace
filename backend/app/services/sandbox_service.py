"""沙箱执行结果的统一分类与持久化辅助。"""

from __future__ import annotations

import mimetypes
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import redis
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
    "resource_busy": "沙箱并发已满，请稍后重试。系统会自动释放失联的执行槽位。",
    "cancelled": "脚本执行已取消。",
    "oom": "脚本因内存不足被终止，请减少内存占用。",
    "pids": "脚本创建的进程数超过限制，请减少并发子进程。",
    "disk": "脚本写入内容超过磁盘限额，请减少临时文件或产物大小。",
    "infra": "沙箱基础设施不可用，请稍后重试。",
    "exit_code": "脚本以非零退出码结束，请检查 stderr 输出。",
}


def acquire_concurrency_slot() -> str | None:
    """尝试获取一个带 TTL 的分布式沙箱槽位；过期租约自动回收。"""
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        for slot in range(settings.sbx_max_concurrency):
            token = f"{slot}:{datetime.now(UTC).timestamp()}"
            key = f"sbx:sem:{slot}"
            if client.set(key, token, nx=True, ex=settings.sbx_timeout_max + 120):
                return token
        return None
    finally:
        client.close()


def release_concurrency_slot(token: str | None) -> None:
    """仅释放当前 token 所持有的槽位，避免误删新租约。"""
    if token is None:
        return
    slot = token.split(":", 1)[0]
    key = f"sbx:sem:{slot}"
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        # compare-and-delete 原子操作，租约过期后不误删别人的槽位
        client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            key,
            token,
        )
    finally:
        client.close()


@contextmanager
def sandbox_slot() -> Iterator[bool]:
    token = acquire_concurrency_slot()
    try:
        yield token is not None
    finally:
        release_concurrency_slot(token)


def finish_payload(result: dict[str, Any]) -> dict[str, Any]:
    """将执行器结果规范化为 step/API 可直接使用的结构。"""
    payload = dict(result)
    category = payload.get("error_category")
    if category and not payload.get("message"):
        payload["message"] = ERROR_MESSAGES.get(category, "沙箱执行失败。")
    payload.setdefault("finished_at", datetime.now(UTC).isoformat())
    return payload


def execution_artifact_truncation(
    step_output: Any, execution_id: str
) -> dict[str, str] | None:
    """仅投影属于该次执行的公共截断信息，不推断旧记录的归属。"""
    if (
        not isinstance(step_output, dict)
        or step_output.get("execution_id") != execution_id
    ):
        return None
    truncated = step_output.get("artifact_truncated")
    if not isinstance(truncated, dict):
        return None
    reason, message = truncated.get("reason"), truncated.get("message")
    if not isinstance(reason, str) or not isinstance(message, str):
        return None
    return {"reason": reason, "message": message}


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
    execution_id: str | None = None,
) -> SandboxExecution:
    """落一条 running 执行记录，并保存代码指纹。"""
    execution = SandboxExecution(
        id=execution_id,
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
    """Deduplicate storage basenames while retaining the visible relative path."""
    directory, separator, basename = name.replace("\\", "/").rpartition("/")
    basename = basename or "unnamed"
    prefix = f"{directory}/" if separator else ""
    used_basenames = {item.replace("\\", "/").split("/")[-1] for item in used}
    if basename not in used_basenames:
        return f"{prefix}{basename}"
    stem, dot, suffix = basename.rpartition(".")
    if not dot:
        stem, suffix = basename, ""
    for index in range(2, 10_000):
        candidate = f"{stem}-{index}.{suffix}" if suffix else f"{stem}-{index}"
        if candidate not in used_basenames:
            return f"{prefix}{candidate}"
    raise AppError("INVALID_INPUT", "制品重名数量超过上限")


def _persist_artifact(
    db: AsyncSession,
    *,
    run_id: str,
    execution_id: str,
    step_id: str | None,
    name: str,
    data: bytes,
    size: int,
    used: set[str],
) -> Artifact:
    filename = _unique_filename(name, used)
    used.add(filename)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    object_key, sha256 = artifact_service.put_bytes(
        run_id, execution_id, filename, data, mime=mime
    )
    artifact = Artifact(
        run_id=run_id,
        step_id=step_id,
        object_key=object_key,
        filename=filename,
        size_bytes=size,
        mime_type=mime,
        sha256=sha256,
    )
    db.add(artifact)
    return artifact


async def persist_sandbox_result(
    db: AsyncSession,
    *,
    run_id: str,
    step_id: str,
    agent_id: str,
    user_id: str,
    payload: dict[str, Any],
    result: dict[str, Any],
) -> SandboxExecution:
    """把沙箱工具返回的事实写入执行表并回收内存中的产物。

    工具在工作线程中运行，容器退出前已把 output/ 归档到 payload；这里在
    Worker 的事务上下文中完成 FK 关联、MinIO 上传和审计，避免把大字段塞进
    steps.output 或 LLM 消息。
    """
    normalized = dict(result)
    status = normalized.get("status", "failed")
    if status == "resource_busy":
        normalized["status"] = "resource"
    execution_id = str(payload.get("execution_id") or "")
    execution = await db.get(SandboxExecution, execution_id) if execution_id else None
    if execution is None:
        execution = await start_execution(
            db,
            run_id=run_id,
            step_id=step_id,
            agent_id=agent_id,
            language=str(payload.get("language", "python")),
            code=str(payload.get("code", "")),
            limits=dict(payload.get("limits") or {}),
            execution_id=execution_id or None,
        )
    await finish_execution(db, execution, normalized, user_id=user_id)

    used: set[str] = set()
    for item in payload.get("artifacts") or []:
        if not isinstance(item, dict) or not isinstance(item.get("data"), bytes):
            continue
        _persist_artifact(
            db,
            run_id=run_id,
            execution_id=execution.id,
            step_id=step_id,
            name=str(item.get("name") or "unnamed"),
            data=item["data"],
            size=int(item.get("size") or len(item["data"])),
            used=used,
        )

    truncated = payload.get("artifact_truncated")
    if truncated:
        await write_audit(
            db,
            user_id=user_id,
            action="artifact_truncated",
            target_type="sandbox_execution",
            target_id=execution.id,
            detail=truncated,
        )
    await db.flush()
    return execution


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
            artifacts.append(
                _persist_artifact(
                    db,
                    run_id=run_id,
                    execution_id=execution_id,
                    step_id=step_id,
                    name=item.name,
                    data=item.data,
                    size=item.size,
                    used=used,
                )
            )
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
