"""需求端点（T046/T055 + 007）：看板列表 + 创建 + 详情 + 附件 + 分配 + 分派动作。

对齐 contracts/api.md 需求部分：
- GET /requests（?scope=relevant|mine&status=&q=&priority=&page=&page_size=）→ 200 RequestList
- POST /requests → 201 Request（status=created）
- GET /requests/{id} → 200 Request（含 attachments + 分派指针 + 经手链）
- POST /requests/{id}/attachments → 201（预签名 PUT URL + 元数据回填）
- GET /requests/{id}/attachments/{key} → 302 预签名 GET URL
- POST /requests/{id}/assign → 202（拖员工=转交；拖 Agent/组=建 Run 入队）
- POST /requests/{id}/accept → 202（007：pending → accepted）
- POST /requests/{id}/reject → 202（007：pending|accepted → created，原因必填）

T049：request_create 审计挂接。T059：assign 审计挂接。007：分派四动作审计。
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.deps import DB, CurrentUser, MemberUser, ResourceId
from app.core.audit import (
    REQUEST_ACCEPT,
    REQUEST_DISPATCH,
    REQUEST_REJECT,
    write_audit,
)
from app.core.events import publish_request_event
from app.models.schedule import Schedule
from app.schemas.dependency import DependencyEdgeList, DependencyEdgeOut
from app.schemas.request import (
    AssignOut,
    AssignRequest,
    AttachmentListOut,
    AttachmentMeta,
    DependencyBrief,
    DispatchActionOut,
    DispatchRecordOut,
    RejectIn,
    RequestCreate,
    RequestList,
    RequestOut,
    RequestUpdate,
)
from app.services import (
    artifact_service,
    assignment_service,
    dependency_graph_service,
    dependency_service,
    dispatch_service,
    request_service,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/requests", tags=["requests"])


async def _publish_request_event(request_id: str, payload: dict) -> None:
    """提交后发布 request 频道事件（看板/详情 WS 实时刷新）。

    失败不阻塞业务（broker 抖动不该让已受理的分派回滚）——与
    assignment_service._publish_assigned 同一条降级纪律。
    """
    try:
        await publish_request_event(request_id, payload)
    except Exception as exc:  # noqa: BLE001 - 事件发布降级，仅告警
        logger.warning(
            "request_event_publish_failed",
            request_id=request_id,
            error=type(exc).__name__,
        )


@router.get("", response_model=RequestList)
async def list_requests(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    scope: str = Query(
        default="relevant",
        pattern="^(relevant|mine)$",
        description="relevant=我创建的+分派给我的（默认）；mine=仅我创建的",
    ),
    # 008：项目维度过滤。project_id=具体项目（含归档，回看不拦）；
    # project=none=未归属。两者互斥（都传以 project_id 为准）。
    project_id: str | None = Query(
        default=None, description="按项目过滤（uuid；不存在/他人 → 404）"
    ),
    project: str | None = Query(
        default=None,
        pattern="^none$",
        description="未归属需求（project_id IS NULL）",
    ),
    status: str | None = Query(
        default=None,
        pattern="^(created|pending|accepted|assigned|running|completed|failed|cancelled)$",
    ),
    q: str | None = Query(default=None, max_length=100),
    priority: str | None = Query(default=None, pattern="^(high|normal|low)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """看板列表（member/admin 可见相关的；viewer 403）。

    007：默认 scope=relevant（我创建的 + 分派给我的）——只扩大不缩小的
    契约变更（spec Assumptions 2）；status 过滤放行 pending/accepted。
    """
    rows, total = await request_service.list_requests(
        db,
        user_id=user.id,
        scope=scope,
        project_id=project_id,
        project=project,
        status=status,
        q=q,
        priority=priority,
        page=page,
        page_size=page_size,
    )
    summaries = await request_service.run_outcome_summaries(
        db, [row.id for row in rows]
    )
    # 当前接收人邮箱一次 IN 查询批量填充（看板卡片可直接显示「在谁手上」）
    emails = await request_service.assignee_emails(
        db, [row.assignee_id for row in rows if row.assignee_id]
    )
    # 009：blocked 批量（一次 IN 查询，不逐条查边——data-model §4）
    # 009：未完成前置概要批量（一次 GROUP BY，不逐条查边——data-model §4）
    unmet = await dependency_service.blocked_summaries(db, [row.id for row in rows])
    return {
        "items": [
            RequestOut.model_validate(row).model_copy(
                update={
                    "runs_summary": summaries.get(row.id),
                    "assignee_email": emails.get(row.assignee_id)
                    if row.assignee_id
                    else None,
                    "blocked": row.id in unmet,
                    "unmet_count": unmet[row.id][0] if row.id in unmet else 0,
                    "unmet_titles": unmet[row.id][1] if row.id in unmet else None,
                }
            )
            for row in rows
        ],
        "total": total,
    }


@router.post("", response_model=RequestOut, status_code=201)
async def create_request(
    payload: RequestCreate, _m: MemberUser, user: CurrentUser, db: DB
):
    """创建需求（member/admin；viewer 403）。"""
    request = await request_service.create_request(db, user_id=user.id, payload=payload)
    await db.refresh(request)
    await write_audit(
        db,
        user_id=user.id,
        action="request_create",
        target_type="request",
        target_id=request.id,
        detail={"title": request.title, "priority": request.priority},
        strict=True,
    )
    await db.commit()
    return RequestOut.model_validate(request)


@router.patch("/{request_id}", response_model=RequestOut)
async def update_request(
    request_id: ResourceId,
    payload: RequestUpdate,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """编辑需求（008 首次接线：该路由在 001 写了 service 但从未暴露）。

    字段级权限分流（FR-005/R-05）：含 `project_id` 键时仅创建者可改
    （经手人 403——他把需求挪去自己项目会让创建者的视图静默丢活）；
    其余字段维持 007 的 owner-or-assignee 谓词。
    """
    # allow_member：010 项目成员也可编辑（与 service 内同一快路）
    before = await request_service.get_request(
        db, request_id, user.id, allow_member=True
    )
    request = await request_service.update_request(
        db, request_id=request_id, user_id=user.id, payload=payload
    )
    # refresh 必须显式：service 的 flush 会让 ORM 属性过期，随后
    # model_validate 读 updated_at 会触发惰性载入（MissingGreenlet，
    # create 端点同款处理）
    await db.refresh(request)
    await write_audit(
        db,
        user_id=user.id,
        action="request_update",
        target_type="request",
        target_id=request_id,
        detail={
            "fields": sorted(payload.model_dump(exclude_unset=True).keys()),
            # 归属变更单独记 from/to：「这件活什么时候换了项目」是项目视图
            # 排查的高频问题，混在 fields 里要翻旧值
            **(
                {
                    "from_project": before.project_id,
                    "to_project": request.project_id,
                }
                if "project_id" in payload.model_dump(exclude_unset=True)
                else {}
            ),
        },
        strict=True,
    )
    await db.commit()
    return RequestOut.model_validate(request)


@router.get("/{request_id}/dependents", response_model=DependencyEdgeList)
async def list_dependents(request_id: ResourceId, user: CurrentUser, db: DB):
    """009 反向视图：谁在等我（依赖我的需求，带标题与状态）。"""
    from app.services.request_service import get_request

    await get_request(db, request_id, user.id)  # 归属校验（404 纪律）
    pairs = await dependency_service.list_dependent_edges(db, request_id)
    # 反向视图的 Out 里 dependency_* 字段语义翻转为「后续方」的信息
    items = [
        DependencyEdgeOut(
            id=edge.id,
            request_id=edge.request_id,
            dependency_id=edge.dependency_id,
            dependency_title=target.title,
            dependency_status=target.status,
            created_at=edge.created_at.isoformat(),
        )
        for edge, target in pairs
    ]
    return {"items": items, "total": len(items)}


@router.get("/{request_id}", response_model=RequestOut)
async def get_request(request_id: ResourceId, user: CurrentUser, db: DB):
    """需求详情（归属校验：创建者或当前接收人，007）。

    含来源调度（004）：`source_schedule_id` + 展示用的 `source_schedule_name`。
    规则被删除后 id 与 name 都为 null，但需求本身与其 Run 全部保留。

    007 新增：`assignee_*` 分派指针 + `dispatch_history` 经手链
    （dispatch/reassign/accept/reject 正序，详情页时间线）。
    """
    # 010：项目成员也可看详情（allow_member 快路；_check_owner 不动）
    request = await request_service.get_request(
        db, request_id, user.id, allow_member=True
    )
    summaries = await request_service.run_outcome_summaries(db, [request.id])
    schedule_name = None
    if request.source_schedule_id:
        schedule = await db.get(Schedule, request.source_schedule_id)
        schedule_name = schedule.name if schedule else None
    assignee_email = None
    if request.assignee_id:
        emails = await request_service.assignee_emails(db, [request.assignee_id])
        assignee_email = emails.get(request.assignee_id)
    history = await request_service.dispatch_history(db, request.id)
    # 经手链的人名展示：一次 IN 查询取齐所有相关用户的邮箱
    # （US1/US2 要求「谁、何时、为什么」——只有 UUID 的经手链说不出「谁」）
    involved_ids = {request.owner_id, request.assignee_id} - {None}
    for h in history:
        involved_ids.update({h.actor_id, h.from_assignee_id, h.to_assignee_id} - {None})
    emails = await request_service.assignee_emails(db, [i for i in involved_ids if i])
    # 009：依赖概要（详情端才有；列表端只给 blocked 布尔，避免 N+1）
    dep_pairs = await dependency_service.list_dependency_edges(db, request.id)
    briefs = [
        DependencyBrief(
            id=edge.id,
            request_id=target.id,
            title=target.title,
            status=target.status,
            terminal=target.status in dependency_service.TERMINAL_STATUSES,
        )
        for edge, target in dep_pairs
    ]
    return RequestOut.model_validate(request).model_copy(
        update={
            "runs_summary": summaries.get(request.id),
            "dependencies": briefs,
            "blocked": any(not b.terminal for b in briefs),
            "unmet_count": sum(1 for b in briefs if not b.terminal),
            "source_schedule_id": request.source_schedule_id,
            "source_schedule_name": schedule_name,
            "assignee_email": assignee_email,
            "dispatch_history": [
                DispatchRecordOut.model_validate(h).model_copy(
                    update={
                        "actor_email": emails.get(h.actor_id),
                        "from_assignee_email": emails.get(h.from_assignee_id)
                        if h.from_assignee_id
                        else None,
                        "to_assignee_email": emails.get(h.to_assignee_id)
                        if h.to_assignee_id
                        else None,
                    }
                )
                for h in history
            ],
        }
    )


@router.post("/{request_id}/accept", response_model=DispatchActionOut, status_code=202)
async def accept_request(
    request_id: ResourceId,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """接收分派给我的需求（007 / US2）：pending → accepted，通知分派人。

    仅当前接收人可接收；非相关人员 404；重复接收 409（service 层拦截）。
    """
    request = await dispatch_service.accept_request(
        db, request_id=request_id, actor_id=user.id
    )
    await write_audit(
        db,
        user_id=user.id,
        action=REQUEST_ACCEPT,
        target_type="request",
        target_id=request_id,
        strict=True,
    )
    await db.commit()
    await _publish_request_event(
        request.id,
        {
            "type": "status_change",
            "status": request.status,
            "assignee_id": request.assignee_id,
            "actor_id": user.id,
        },
    )
    return DispatchActionOut(request_id=request.id, status=request.status)


@router.post("/{request_id}/reject", response_model=DispatchActionOut, status_code=202)
async def reject_request(
    request_id: ResourceId,
    payload: RejectIn,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """退回分派来的需求（007 / US2）：pending|accepted → created，原因必填。

    创建者可撤回分派；接收人可附原因退回。执行中/终态 409。
    """
    request = await dispatch_service.reject_request(
        db, request_id=request_id, actor_id=user.id, reason=payload.reason
    )
    await write_audit(
        db,
        user_id=user.id,
        action=REQUEST_REJECT,
        target_type="request",
        target_id=request_id,
        detail={"reason": payload.reason},
        strict=True,
    )
    await db.commit()
    await _publish_request_event(
        request.id,
        {
            "type": "status_change",
            "status": request.status,
            "assignee_id": request.assignee_id,
            "actor_id": user.id,
        },
    )
    return DispatchActionOut(request_id=request.id, status=request.status)


@router.get("/{request_id}/dependency-graph", response_model=dict)
async def get_dependency_graph(
    request_id: ResourceId, user: CurrentUser, _m: MemberUser, db: DB
):
    """依赖图谱（021）：下游子图 + 拓扑序 + 关键路径。

    读权限与需求详情一致（owner ∪ assignee ∪ 项目参与者）；子图里可能包含
    别人的需求，这里**不逐节点鉴权**——依赖边本身已经决定了「能看到这条链」，
    而标题不是敏感数据（同 009 的 R-05：依赖目标不做可见性校验）。
    """
    await request_service.get_request(db, request_id, user.id, allow_member=True)
    return await dependency_graph_service.downstream_graph(db, request_id)


@router.post(
    "/{request_id}/attachments", response_model=AttachmentListOut, status_code=201
)
async def upload_attachment(
    request_id: ResourceId,
    user: CurrentUser,
    db: DB,
    filename: str = Query(min_length=1, max_length=255),
    size: int = Query(ge=1),
    mime_type: str = Query(min_length=1, max_length=100),
):
    """附件上传：校验 + POST Policy 上传凭据 + 元数据回填（归属校验）。"""
    request = await request_service.get_request(db, request_id, user.id)

    put = await artifact_service.presign_upload_async(
        request_id, filename=filename, size=size, mime_type=mime_type
    )

    # 元数据回填 requests.attachments
    meta = AttachmentMeta(key=put.key, name=put.name, size=put.size, mime=put.mime)
    current = list(request.attachments or [])
    current.append(meta.model_dump())
    request.attachments = current
    # flush 必须显式：session 是 autoflush=False，且 refresh 会丢弃未 flush 的改动
    await db.flush()
    await db.refresh(request)

    await write_audit(
        db,
        user_id=user.id,
        action="attachment_upload",
        target_type="request",
        target_id=request_id,
        detail={"filename": put.name, "size": put.size, "key": put.key},
        strict=True,
    )
    await db.commit()

    return {"attachments": [put]}


@router.get("/{request_id}/attachments/{key:path}", response_class=RedirectResponse)
async def download_attachment(
    request_id: ResourceId,
    key: str,
    user: CurrentUser,
    db: DB,
    redirect: Annotated[
        bool, Query(description="false 时返回 JSON URL 而不是 302")
    ] = True,
):
    """附件下载（归属校验）→ 302 预签名 GET URL。

    ``redirect=false`` 返回 ``{"url": ...}``，供浏览器端先用带鉴权的请求换签名
    URL 再跳转（``<a href>`` 无法携带 Bearer 头，直连只会 401）。
    """
    request = await request_service.get_request(db, request_id, user.id)
    normalized = artifact_service.normalize_key(key)
    url = await artifact_service.download_url_async(
        list(request.attachments or []), normalized
    )
    if not redirect:
        return JSONResponse({"url": url, "filename": normalized.rsplit("/", 1)[-1]})
    return RedirectResponse(url, status_code=302)


@router.post(
    "/{request_id}/assign",
    response_model=AssignOut | DispatchActionOut,
    status_code=202,
)
async def assign_request(
    request_id: ResourceId,
    payload: AssignRequest,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """分配（T055 + 007）：拖到 Agent/组 → 建 Run 执行；拖到员工 → 仅转交责任。

    - 拖单 Agent：strategy 可省（默认 direct）
    - 拖组：strategy 可省（默认取组配置）
    - 拖员工（007）：不建 Run、不入队，需求 → pending，收件箱 + 通知；
      首次分派与转交同入口（pending 时再拖 = 转交）
    - 202；409 已在执行/未接收；400 归档目标/未实现策略；
      404 目标不存在/无权限；429 需求 owner 配额超限（007 改记 owner）
    """
    if payload.assignee_type.value == "user":
        # 员工落点：责任转交（dispatch_service：行级锁 + 配额门 + 状态机 +
        # 历史 + 通知）。判定顺序见 contracts §3。
        request = await dispatch_service.dispatch_request(
            db,
            request_id=request_id,
            actor_id=user.id,
            target_user_id=payload.assignee_id,
            note=payload.note,
        )
        await write_audit(
            db,
            user_id=user.id,
            action=REQUEST_DISPATCH,
            target_type="request",
            target_id=request_id,
            detail={
                # dispatch/reassign 的区分由 service 按「当前有无接收人」判定，
                # 精确动作在 request_dispatches 历史表；审计这里记落点与留言。
                "to_assignee_id": payload.assignee_id,
                "note": payload.note,
            },
            strict=True,
        )
        await db.commit()
        await _publish_request_event(
            request.id,
            {
                "type": "status_change",
                "status": request.status,
                "assignee_id": request.assignee_id,
                "actor_id": user.id,
            },
        )
        return DispatchActionOut(request_id=request.id, status=request.status)

    parent_run_id, runs, strategy = await assignment_service.assign(
        db,
        request_id=request_id,
        user_id=user.id,
        assignee_type=payload.assignee_type.value,
        assignee_id=payload.assignee_id,
        strategy=payload.strategy,
    )
    run_ids = [run.id for run in runs]

    # T059 审计：assign（detail 含 assignee_type/assignee_id/strategy）
    await write_audit(
        db,
        user_id=user.id,
        action="assign",
        target_type="request",
        target_id=request_id,
        detail={
            "assignee_type": payload.assignee_type.value,
            "assignee_id": payload.assignee_id,
            "strategy": payload.strategy or "auto",
            "run_ids": run_ids,
        },
        strict=True,
    )
    # 需求、Run 与审计同一事务；入队必须在提交之后，否则 Worker 可能读不到 Run
    await db.commit()
    await assignment_service.dispatch_assigned(runs, strategy=strategy)

    return AssignOut(run_id=parent_run_id, status="queued")
