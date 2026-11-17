"""运行端点（T070/T074，US5）：详情 + 取消/重试/回复 + 制品下载。

对齐 contracts/api.md 运行部分（FR-016~019/FR-021）：
- GET /runs/{id} → 200（含 steps/cost/result/error/artifacts）
- POST /runs/{id}/cancel → 200 {"status": "cancelled"}；终态 409（FR-018）
- POST /runs/{id}/retry → 202 {"status": "queued"}（仅 failed/timeout，FR-017）
- POST /runs/{id}/reply → 200 {"status": "running"}（仅 needs_input，FR-021）
- GET /runs/{id}/artifacts/{artifact_id} → 302 预签名（FR-016）

T074：run_cancel / run_retry / run_reply 审计挂接（detail 含 from_status）。
"""

from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.api.deps import DB, CurrentUser, MemberUser, ResourceId
from app.api.errors import AppError
from app.core.audit import write_audit
from app.models.artifact import Artifact
from app.models.egress import EgressAccess
from app.models.run import Run
from app.models.sandbox_execution import SandboxExecution
from app.models.step import Step
from app.orchestrator.state_machine import RunStatus, transition
from app.schemas.run import RunOut, RunReplyIn
from app.schemas.sandbox import EgressAccessList, ExecutionDetail, ExecutionSummary
from app.services import artifact_service
from app.services.assignment_service import _enqueue
from app.services.external_invocation_service import list_run_invocations
from app.services.request_service import settle_request_status
from app.services.sandbox_service import execution_artifact_truncation

router = APIRouter(prefix="/runs", tags=["runs"])


async def _get_owned_run(db: DB, run_id: str, user: CurrentUser) -> Run:
    """归属校验：Run 必须存在且 owner 是当前用户，否则 404（信息隐藏，不暴露存在性）。"""
    run = await db.get(Run, run_id)
    if run is None or run.owner_id != user.id:
        raise AppError("NOT_FOUND", "运行不存在")
    return run


def _conflict(detail: str) -> AppError:
    return AppError("CONFLICT", detail)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: ResourceId, user: CurrentUser, db: DB):
    """运行详情（归属校验）：含 steps 列表摘要、cost、result、error、artifacts。"""
    run = await _get_owned_run(db, run_id, user)

    # Polling returns summaries, not potentially large code/output/JSON payloads.
    # Raise on accidental deferred access instead of silently adding lazy I/O.
    steps = (
        (
            await db.execute(
                select(Step)
                .where(Step.run_id == run.id)
                .order_by(Step.seq)
                .options(
                    load_only(
                        Step.id,
                        Step.seq,
                        Step.kind,
                        Step.status,
                        Step.tool_name,
                        Step.duration_ms,
                        Step.cost_cents,
                        Step.created_at,
                        raiseload=True,
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    artifacts = (
        (
            await db.execute(
                select(Artifact)
                .where(Artifact.run_id == run.id)
                .options(
                    load_only(
                        Artifact.id,
                        Artifact.step_id,
                        Artifact.filename,
                        Artifact.size_bytes,
                        Artifact.mime_type,
                        Artifact.sha256,
                        Artifact.created_at,
                        raiseload=True,
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    executions = (
        (
            await db.execute(
                select(SandboxExecution)
                .where(SandboxExecution.run_id == run.id)
                .options(
                    load_only(
                        SandboxExecution.id,
                        SandboxExecution.step_id,
                        SandboxExecution.language,
                        SandboxExecution.status,
                        SandboxExecution.exit_code,
                        SandboxExecution.duration_ms,
                        SandboxExecution.stdout_truncated,
                        raiseload=True,
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    step_seq = {step.id: step.seq for step in steps}
    artifact_counts = Counter(artifact.step_id for artifact in artifacts)
    execution_summaries = [
        ExecutionSummary(
            id=execution.id,
            step_seq=step_seq.get(execution.step_id, 0),
            language=execution.language,
            status=execution.status,
            exit_code=execution.exit_code,
            duration_ms=execution.duration_ms,
            stdout_truncated=execution.stdout_truncated,
            artifacts=artifact_counts[execution.step_id],
        )
        for execution in executions
    ]
    external_tool_invocations = await list_run_invocations(db, run_id=run.id)
    return RunOut.model_validate(
        {
            **run.__dict__,
            "steps": steps,
            "artifacts": artifacts,
            "executions": execution_summaries,
            "external_tool_invocations": external_tool_invocations,
        }
    )


@router.get("/{run_id}/executions/{execution_id}", response_model=ExecutionDetail)
async def get_execution(
    run_id: ResourceId, execution_id: ResourceId, user: CurrentUser, db: DB
):
    """查看一次沙箱执行的完整详情（代码/输出仅在此接口返回）。"""
    run = await _get_owned_run(db, run_id, user)
    execution = await db.get(SandboxExecution, execution_id)
    if execution is None or execution.run_id != run.id:
        raise AppError("NOT_FOUND", "沙箱执行不存在")
    step = await db.get(Step, execution.step_id)
    artifacts = (
        (
            await db.execute(
                select(Artifact).where(Artifact.step_id == execution.step_id)
            )
        )
        .scalars()
        .all()
    )
    return ExecutionDetail.model_validate(
        {
            **execution.__dict__,
            "step_seq": step.seq if step else 0,
            "artifact_truncated": execution_artifact_truncation(
                step.output if step else None, execution.id
            ),
            "artifacts": [
                {
                    "id": artifact.id,
                    "filename": artifact.filename,
                    "size_bytes": artifact.size_bytes,
                }
                for artifact in artifacts
            ],
        }
    )


@router.get(
    "/{run_id}/executions/{execution_id}/egress", response_model=EgressAccessList
)
async def get_execution_egress(
    run_id: ResourceId, execution_id: ResourceId, user: CurrentUser, db: DB
):
    """查看某次执行的网络访问记录。"""
    run = await _get_owned_run(db, run_id, user)
    execution = await db.get(SandboxExecution, execution_id)
    if execution is None or execution.run_id != run.id:
        raise AppError("NOT_FOUND", "沙箱执行不存在")
    rows = (
        (
            await db.execute(
                select(EgressAccess)
                .where(EgressAccess.execution_id == execution_id)
                .order_by(EgressAccess.occurred_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return {"items": rows}


# 可取消的非终态：取消一个 Run 时，它的后代只要能停就一起停。
_CANCELLABLE = frozenset({"queued", "running", "needs_input"})


async def _cancel_descendants(db: AsyncSession, root_id: str) -> list[str]:
    """取消该 Run 的所有非终态后代（BFS，容忍任意深度）。

    fan_out / pipeline 的父 Run 只是编排占位，真正花钱的是子 Run；只把父 Run 置为
    cancelled 时子 Run 会继续调 LLM、跑沙箱并累计成本（FR-018 的「用户取消后必须
    真的停下」就不成立）。
    """
    cancelled: list[str] = []
    frontier = [root_id]
    seen: set[str] = set()
    while frontier:
        parent_id = frontier.pop()
        if parent_id in seen:
            continue
        seen.add(parent_id)
        children = (
            (await db.execute(select(Run).where(Run.parent_run_id == parent_id)))
            .scalars()
            .all()
        )
        for child in children:
            frontier.append(child.id)
            if child.status in _CANCELLABLE:
                transition(child, RunStatus.CANCELLED)
                cancelled.append(child.id)
    return cancelled


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: ResourceId, _m: MemberUser, user: CurrentUser, db: DB):
    """取消运行（FR-018）：queued/running/needs_input → cancelled；终态 409。

    取消会级联到所有非终态子 Run。唯一的例外是「已终态但仍挂着在跑的子 Run」的
    编排父 Run（fan_out 的父 Run 建好即 completed）：此时父 Run 自己的状态改不动，
    但调用仍然成功并把子 Run 停下——否则前端只能拿到父 Run id，永远停不下这棵树。
    """
    run = await _get_owned_run(db, run_id, user)
    from_status = run.status
    cancellable = from_status in _CANCELLABLE
    cancelled_children = await _cancel_descendants(db, run.id)
    if not cancellable and not cancelled_children:
        # 终态且没有可取消的后代：维持 409（终态不可迁移）
        raise _conflict("当前状态不可取消")
    if cancellable:
        transition(run, RunStatus.CANCELLED)

    await write_audit(
        db,
        user_id=user.id,
        action="run_cancel",
        target_type="run",
        target_id=run.id,
        detail={
            "from_status": from_status,
            "cancelled_descendants": len(cancelled_children),
        },
    )
    # 取消后需求也要收口，否则看板上这张卡片永远停在「执行中」
    await settle_request_status(db, run.request_id)
    await db.commit()
    return {
        "status": "cancelled" if cancellable else run.status,
        "cancelled_runs": 1 + len(cancelled_children)
        if cancellable
        else len(cancelled_children),
    }


@router.post("/{run_id}/retry", status_code=202)
async def retry_run(run_id: ResourceId, _m: MemberUser, user: CurrentUser, db: DB):
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
async def reply_run(run_id: ResourceId, payload: RunReplyIn, user: CurrentUser, db: DB):
    """回复运行（FR-021）：needs_input → running（继续执行）；其余 409。

    用户回复内容保存到 run.user_reply，恢复执行时注入对话上下文（缺陷 A 修复）。
    """
    run = await _get_owned_run(db, run_id, user)
    from_status = run.status
    # 幂等分支会吞掉 running→running，必须显式校验前置状态（FR-021）
    if run.status != "needs_input":
        raise _conflict("仅 needs_input 状态可回复")
    # schema 已挡掉空串；全空白仍需业务层判定（" " 通过 min_length）
    message = payload.message.strip()
    if not message:
        raise _conflict("回复内容不能为空")
    # 保存用户回复（FR-021）：供恢复执行时注入对话上下文
    run.user_reply = message
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
    run_id: ResourceId,
    artifact_id: ResourceId,
    user: CurrentUser,
    db: DB,
    redirect: Annotated[
        bool, Query(description="false 时返回 JSON URL 而不是 302")
    ] = True,
):
    """制品下载（归属校验）→ 302 预签名 GET URL（FR-016）。

    ``redirect=false`` 返回 ``{"url": ...}``：浏览器里的 ``<a href>`` 无法附加
    Bearer 头，链接直连本端点只会拿到 401。前端先用带鉴权的请求换取签名 URL，
    再跳转，才能既保留归属校验又不把 token 交给对象存储。
    """
    run = await _get_owned_run(db, run_id, user)
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None or artifact.run_id != run.id:
        raise AppError("NOT_FOUND", "制品不存在")
    url = await artifact_service.presign_get_async(artifact.object_key)
    if not redirect:
        return JSONResponse(
            {
                "url": url,
                "filename": artifact.filename,
                "size_bytes": artifact.size_bytes,
            }
        )
    return RedirectResponse(url, status_code=302)
