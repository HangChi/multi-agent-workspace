"""邀请码申请服务（027）。

批准走的是 022 的 `create_invite_codes`——**不另造一条发码路径**：部门月度配额、
「整批校验不留半批」这两条规则自动继承。审批流程加的是过程与留痕，不是新的铸码口。
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.invite_request import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    InviteRequest,
)
from app.services import invite_code_service
from app.services.notification_service import create_notification

logger = structlog.get_logger(__name__)

MAX_PENDING_PER_USER = 3


async def create_request(
    db: AsyncSession,
    *,
    requester_id: str,
    role: str,
    count: int,
    reason: str,
    department_id: str | None = None,
) -> InviteRequest:
    """提交申请。同一人最多 3 条待审批（防止刷屏把队列淹了）。"""
    reason = reason.strip()
    if not reason:
        raise AppError("INVALID_INPUT", "请写一句申请理由")
    pending = (
        (
            await db.execute(
                select(InviteRequest.id).where(
                    InviteRequest.requester_id == requester_id,
                    InviteRequest.status == STATUS_PENDING,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(pending) >= MAX_PENDING_PER_USER:
        raise AppError("CONFLICT", f"你已有 {len(pending)} 条待审批申请，请等待处理")

    request = InviteRequest(
        requester_id=requester_id,
        department_id=department_id,
        role=role,
        count=count,
        reason=reason,
        status=STATUS_PENDING,
    )
    db.add(request)
    await db.flush()
    logger.info("invite_request_created", request_id=request.id, count=count)
    return request


async def list_requests(
    db: AsyncSession, *, user_id: str, is_admin: bool, status: str | None = None
) -> list[InviteRequest]:
    """admin 看全部（默认只看待审批）；非 admin 只看自己的。"""
    stmt = select(InviteRequest).order_by(InviteRequest.created_at.desc())
    if not is_admin:
        stmt = stmt.where(InviteRequest.requester_id == user_id)
    elif status is not None:
        stmt = stmt.where(InviteRequest.status == status)
    return list((await db.execute(stmt.limit(200))).scalars().all())


async def approve(
    db: AsyncSession, *, request_id: str, admin_id: str
) -> tuple[InviteRequest, list[str]]:
    """批准并立刻发码；配额不足时申请保持 pending（400/409 由 022 抛出）。"""
    request = await db.get(InviteRequest, request_id, with_for_update=True)
    if request is None:
        raise AppError("NOT_FOUND", "申请不存在")
    if request.status != STATUS_PENDING:
        raise AppError("CONFLICT", "该申请已被处理")

    rows = await invite_code_service.create_invite_codes(
        db,
        count=request.count,
        role=request.role,
        max_uses=1,  # 申请的是「几个人」，一个人一张码
        expires_in_days=None,
        created_by=admin_id,
        department_id=request.department_id,
    )
    codes = [row.code for row in rows]
    request.status = STATUS_APPROVED
    request.decided_by = admin_id
    request.decided_at = datetime.now(UTC)
    request.issued_codes = "\n".join(codes)
    await db.flush()

    await create_notification(
        db,
        user_id=request.requester_id,
        type_="invite_request_approved",
        title=f"你的邀请码申请已通过（{len(codes)} 个）",
        payload={"request_id": request.id, "count": len(codes)},
    )
    logger.info("invite_request_approved", request_id=request.id, codes=len(codes))
    return request, codes


async def reject(
    db: AsyncSession, *, request_id: str, admin_id: str, reason: str | None = None
) -> InviteRequest:
    request = await db.get(InviteRequest, request_id, with_for_update=True)
    if request is None:
        raise AppError("NOT_FOUND", "申请不存在")
    if request.status != STATUS_PENDING:
        raise AppError("CONFLICT", "该申请已被处理")

    request.status = STATUS_REJECTED
    request.decided_by = admin_id
    request.decided_at = datetime.now(UTC)
    await db.flush()

    await create_notification(
        db,
        user_id=request.requester_id,
        type_="invite_request_rejected",
        title="你的邀请码申请未通过",
        payload={"request_id": request.id, "reason": reason},
    )
    return request
