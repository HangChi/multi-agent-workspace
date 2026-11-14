"""需求端点（T046/T055）：看板列表 + 创建 + 详情 + 附件 + 分配。

对齐 contracts/api.md 需求部分：
- GET /requests（?status=&q=&priority=&page=&page_size=）→ 200 RequestList
- POST /requests → 201 Request（status=created）
- GET /requests/{id} → 200 Request（含 attachments）
- POST /requests/{id}/attachments → 201（预签名 PUT URL + 元数据回填）
- GET /requests/{id}/attachments/{key} → 302 预签名 GET URL
- POST /requests/{id}/assign → 202（仅校验+入队，RT<100ms）

T049：request_create 审计挂接。T059：assign 审计挂接。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse

from app.api.deps import DB, CurrentUser, require_role
from app.core.audit import write_audit
from app.schemas.request import (
    AssignOut,
    AssignRequest,
    AttachmentListOut,
    AttachmentMeta,
    RequestCreate,
    RequestList,
    RequestOut,
)
from app.services import artifact_service, assignment_service, request_service

router = APIRouter(prefix="/requests", tags=["requests"])

MemberUser = Annotated[object, Depends(require_role("admin", "member"))]


@router.get("", response_model=RequestList)
async def list_requests(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    status: str | None = Query(
        default=None, pattern="^(created|assigned|running|completed|failed|cancelled)$"
    ),
    q: str | None = Query(default=None, max_length=100),
    priority: str | None = Query(default=None, pattern="^(high|normal|low)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """看板列表（member/admin 可见自己的；viewer 403）。"""
    rows, total = await request_service.list_requests(
        db,
        user_id=user.id,
        status=status,
        q=q,
        priority=priority,
        page=page,
        page_size=page_size,
    )
    return {"items": [RequestOut.model_validate(r) for r in rows], "total": total}


@router.post("", response_model=RequestOut, status_code=201)
async def create_request(
    payload: RequestCreate, _m: MemberUser, user: CurrentUser, db: DB
):
    """创建需求（member/admin；viewer 403）。"""
    request = await request_service.create_request(db, user_id=user.id, payload=payload)
    await db.commit()
    await db.refresh(request)
    await write_audit(
        db,
        user_id=user.id,
        action="request_create",
        target_type="request",
        target_id=request.id,
        detail={"title": request.title, "priority": request.priority},
    )
    await db.commit()
    return RequestOut.model_validate(request)


@router.get("/{request_id}", response_model=RequestOut)
async def get_request(request_id: str, user: CurrentUser, db: DB):
    """需求详情（归属校验）。"""
    request = await request_service.get_request(db, request_id, user.id)
    return RequestOut.model_validate(request)


@router.post("/{request_id}/attachments", response_model=AttachmentListOut, status_code=201)
async def upload_attachment(
    request_id: str,
    user: CurrentUser,
    db: DB,
    filename: str = Query(min_length=1, max_length=255),
    size: int = Query(ge=1),
    mime_type: str = Query(min_length=1, max_length=100),
):
    """附件上传：校验 + 预签名 PUT URL + 元数据回填（归属校验）。"""
    request = await request_service.get_request(db, request_id, user.id)

    put = artifact_service.presign_put(
        request_id, filename=filename, size=size, mime_type=mime_type
    )

    # 元数据回填 requests.attachments
    meta = AttachmentMeta(key=put.key, name=put.name, size=put.size, mime=put.mime)
    current = list(request.attachments or [])
    current.append(meta.model_dump())
    request.attachments = current
    await db.commit()
    await db.refresh(request)

    await write_audit(
        db,
        user_id=user.id,
        action="attachment_upload",
        target_type="request",
        target_id=request_id,
        detail={"filename": put.name, "size": put.size, "key": put.key},
    )
    await db.commit()

    return {"attachments": [put]}


@router.get("/{request_id}/attachments/{key:path}", response_class=RedirectResponse)
async def download_attachment(
    request_id: str,
    key: str,
    user: CurrentUser,
    db: DB,
):
    """附件下载（归属校验）→ 302 预签名 GET URL。"""
    request = await request_service.get_request(db, request_id, user.id)
    normalized = artifact_service.normalize_key(key)
    url = artifact_service.download_url(request, normalized)
    return RedirectResponse(url, status_code=302)


@router.post("/{request_id}/assign", response_model=AssignOut, status_code=202)
async def assign_request(
    request_id: str,
    payload: AssignRequest,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """分配（T055）：拖到 Agent/组，校验 + 建 Run 入队，需求变执行中。

    - 拖单 Agent：strategy 可省（默认 direct）
    - 拖组：strategy 可省（默认取组配置）
    - 202 {"run_id","status":"queued"}；409 已在执行；400 归档目标/未实现策略；
      404 目标不存在/无权限
    """
    parent_run_id, run_ids = await assignment_service.assign(
        db,
        request_id=request_id,
        user_id=user.id,
        assignee_type=payload.assignee_type.value,
        assignee_id=payload.assignee_id,
        strategy=payload.strategy,
    )

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
    )
    await db.commit()

    return AssignOut(run_id=parent_run_id, status="queued")
