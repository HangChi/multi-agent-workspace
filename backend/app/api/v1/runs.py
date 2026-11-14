"""运行端点（T070/T074，US5）：详情 + 取消/重试/回复 + 制品下载。

对齐 contracts/api.md 运行部分（FR-016~019/FR-021）：
- GET /runs/{id} → 200（含 steps/cost/result/error/artifacts）
- POST /runs/{id}/cancel → 200 {"status": "cancelled"}；终态 409（FR-018）
- POST /runs/{id}/retry → 202 {"status": "queued"}（仅 failed/timeout，FR-017）
- POST /runs/{id}/reply → 200 {"status": "running"}（仅 needs_input，FR-021）
- GET /runs/{id}/artifacts/{artifact_id} → 302 预签名（FR-016）

T074：run_cancel / run_retry / run_reply 审计挂接（detail 含 from_status）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, require_role
from app.api.errors import AppError
from app.core.audit import write_audit
from app.models.artifact import Artifact
from app.models.run import Run
from app.models.sandbox_execution import SandboxExecution
from app.models.step import Step
from app.orchestrator.state_machine import RunStatus, transition
from app.schemas.run import RunOut
from app.schemas.sandbox import ExecutionDetail, ExecutionSummary
from app.services import artifact_service
from app.services.assignment_service import _enqueue

router = APIRouter(prefix="/runs", tags=["runs"])

MemberUser = Annotated[object, Depends(require_role("admin", "member"))]


async def _get_owned_run(db: DB, run_id: str, user: CurrentUser) -> Run:
    """归属校验：Run 必须存在且 owner 是当前用户，否则 404（信息隐藏，不暴露存在性）。"""
    run = await db.get(Run, run_id)
    if run is None or run.owner_id != user.id:
        raise AppError("NOT_FOUND", "运行不存在")
    return run


def _conflict(detail: str) -> AppError:
    return AppError("CONFLICT", detail)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, user: CurrentUser, db: DB):
    """运行详情（归属校验）：含 steps 列表摘要、cost、result、error、artifacts。"""
    run = await _get_owned_run(db, run_id, user)

    steps = (
        (await db.execute(select(Step).where(Step.run_id == run.id).order_by(Step.seq)))
        .scalars()
        .all()
    )
    artifacts = (
        (await db.execute(select(Artifact).where(Artifact.run_id == run.id)))
        .scalars()
        .all()
    )
    executions = (
        (await db.execute(select(SandboxExecution).where(SandboxExecution.run_id == run.id)))
        .scalars()
        .all()
    )
    step_seq = {step.id: step.seq for step in steps}
    execution_summaries = [
        ExecutionSummary(
            id=execution.id,
            step_seq=step_seq.get(execution.step_id, 0),
            language=execution.language,
            status=execution.status,
            exit_code=execution.exit_code,
            duration_ms=execution.duration_ms,
            stdout_truncated=execution.stdout_truncated,
            artifacts=sum(1 for artifact in artifacts if artifact.step_id == execution.step_id),
        )
        for execution in executions
    ]
    return RunOut.model_validate(
        {
            **run.__dict__,
            "steps": steps,
            "artifacts": artifacts,
            "executions": execution_summaries,
        }
    )


@router.get("/{run_id}/executions/{execution_id}", response_model=ExecutionDetail)
async def get_execution(run_id: str, execution_id: str, user: CurrentUser, db: DB):
    """查看一次沙箱执行的完整详情（代码/输出仅在此接口返回）。"""
    run = await _get_owned_run(db, run_id, user)
    execution = await db.get(SandboxExecution, execution_id)
    if execution is None or execution.run_id != run.id:
        raise AppError("NOT_FOUND", "沙箱执行不存在")
    step = await db.get(Step, execution.step_id)
    artifacts = (
        await db.execute(select(Artifact).where(Artifact.step_id == execution.step_id))
    ).scalars().all()
    return ExecutionDetail.model_validate(
        {
            **execution.__dict__,
            "step_seq": step.seq if step else 0,
            "artifacts": artifacts,
        }
    )


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, _m: MemberUser, user: CurrentUser, db: DB):
    """取消运行（FR-018）：queued/running/needs_input → cancelled；终态 409。"""
    run = await _get_owned_run(db, run_id, user)
    from_status = run.status
    # 显式前置校验：仅 queued/running/needs_input 可取消（幂等分支会吞掉 cancelled→cancelled）
    if run.status not in {"queued", "running", "needs_input"}:
        raise _conflict("当前状态不可取消")
    transition(run, RunStatus.CANCELLED)

    await write_audit(
        db,
        user_id=user.id,
        action="run_cancel",
        target_type="run",
        target_id=run.id,
        detail={"from_status": from_status},
    )
    await db.commit()
    return {"status": "cancelled"}


@router.post("/{run_id}/retry", status_code=202)
async def retry_run(run_id: str, _m: MemberUser, user: CurrentUser, db: DB):
    """重试运行（FR-017）：failed/timeout → queued（重置执行现场）；其余 409。"""
    run = await _get_owned_run(db, run_id, user)
    from_status = run.status
    # 显式前置校验：仅 failed/timeout 可重试（幂等分支会吞掉 queued→queued）
    if run.status not in {"failed", "timeout"}:
        raise _conflict("仅 failed/timeout 状态可重试")
    transition(run, RunStatus.QUEUED)

    await write_audit(
        db,
        user_id=user.id,
        action="run_retry",
        target_type="run",
        target_id=run.id,
        detail={"from_status": from_status},
    )
    await db.commit()
    # 重新入队（FR-017）：failed → queued 后必须让 Worker 再次消费
    _enqueue([run.id])
    return {"status": "queued"}


@router.post("/{run_id}/reply")
async def reply_run(run_id: str, user: CurrentUser, db: DB, payload: dict | None = None):
    """回复运行（FR-021）：needs_input → running（继续执行）；其余 409。

    用户回复内容保存到 run.user_reply，恢复执行时注入对话上下文（缺陷 A 修复）。
    """
    run = await _get_owned_run(db, run_id, user)
    from_status = run.status
    # 幂等分支会吞掉 running→running，必须显式校验前置状态（FR-021）
    if run.status != "needs_input":
        raise _conflict("仅 needs_input 状态可回复")
    message = (payload or {}).get("message")
    if not message or not str(message).strip():
        raise _conflict("回复内容不能为空")
    # 保存用户回复（FR-021）：供恢复执行时注入对话上下文
    run.user_reply = str(message).strip()
    transition(run, RunStatus.RUNNING)

    await write_audit(
        db,
        user_id=user.id,
        action="run_reply",
        target_type="run",
        target_id=run.id,
        detail={"from_status": from_status, "message": message},
    )
    await db.commit()
    # 回复后重新入队（FR-021）：needs_input → running 后必须让 Worker 继续执行
    _enqueue([run.id])
    return {"status": "running"}


@router.get("/{run_id}/artifacts/{artifact_id}", response_class=RedirectResponse)
async def download_artifact(
    run_id: str,
    artifact_id: str,
    user: CurrentUser,
    db: DB,
):
    """制品下载（归属校验）→ 302 预签名 GET URL（FR-016）。"""
    run = await _get_owned_run(db, run_id, user)
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None or artifact.run_id != run.id:
        raise AppError("NOT_FOUND", "制品不存在")
    url = artifact_service.presign_get(artifact.object_key)
    return RedirectResponse(url, status_code=302)
