"""分派服务（T015，007 / US1-US2）：dispatch / accept / reject 纯增量逻辑。

与 assignment_service 的分工：本服务只做「责任转交」（改 requests 的责任
指针 + 历史 + 通知），**不建 Run、不入队、不发 run 事件**——重活归执行引擎
的纪律（宪法 §架构）在这里的含义是：分派受理必须比 agent 分配更轻。

事务纪律同 assign：service 只 flush，调用方（端点）写审计后统一 commit，
审计与业务因此落在同一事务里（docs/code-audit #9）。

行级锁：三个入口都先 `SELECT ... FOR UPDATE` 再读状态——「同时接收/退回」
双赢是本特性最高优先级并发缺陷（SC-004），锁是唯一硬保证。
"""

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core import metrics
from app.models.request import Request
from app.models.request_dispatch import RequestDispatch
from app.models.user import User
from app.orchestrator.request_state_machine import (
    DISPATCHABLE_STATUSES,
    RequestStatus,
    assert_dispatch_invariants,
    transition,
)

logger = structlog.get_logger(__name__)


async def _load_request_locked(db: AsyncSession, request_id: str) -> Request:
    """行级锁加载需求：不存在 → 404（与 get_request 的归属纪律一致，
    先锁后判，状态检查与迁移因此原子）。"""
    stmt = select(Request).where(Request.id == request_id).with_for_update()
    request = (await db.execute(stmt)).scalars().first()
    if request is None:
        raise AppError("NOT_FOUND", "需求不存在")
    return request


def _check_access(request: Request, actor_id: str) -> None:
    """资源访问权：创建者或当前接收人。其他人一律 404（不暴露存在性）。"""
    if request.owner_id != actor_id and request.assignee_id != actor_id:
        raise AppError("NOT_FOUND", "需求不存在")


async def _load_active_user(db: AsyncSession, user_id: str) -> User:
    """目标用户校验：不存在或已禁用 → 404（禁用账号不可作为接收人，US6）。"""
    user = await db.get(User, user_id)
    if user is None or user.status != "active":
        raise AppError("NOT_FOUND", "用户不存在")
    return user


async def _last_dispatcher_id(db: AsyncSession, request_id: str) -> str | None:
    """最近一次分派/转交的操作人——通知的接收方（「谁派给我的找谁」）。"""
    stmt = (
        select(RequestDispatch.actor_id)
        .where(
            RequestDispatch.request_id == request_id,
            RequestDispatch.action.in_(("dispatch", "reassign")),
        )
        .order_by(RequestDispatch.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def _notify(
    db: AsyncSession,
    *,
    user_id: str,
    type_: str,
    request: Request,
    payload: dict,
) -> None:
    """写一条站内通知。「不通知自己」的降噪在调用点判断（自派自接等场景）。

    title 存需求标题快照：标题可改，通知是「当时发生了什么」的记录
    （data-model §4）。

    015：写入与实时推送统一走 notification_service.create_notification——
    此前四处各写一遍 db.add，加推送时漏一处就是「这条通知永远晚半分钟」。
    """
    from app.services.notification_service import create_notification

    await create_notification(
        db,
        user_id=user_id,
        type_=type_,
        title=request.title,
        request_id=request.id,
        payload=payload,
    )


async def _next_seq(db: AsyncSession, request_id: str) -> int:
    """经手链下一个序号（每需求单调）。

    调用方全部持有 request 行锁（本服务三个入口都先 FOR UPDATE），
    因此 max+1 无竞争——这也正是 seq 比 created_at 可靠的原因：
    PG 的 now() 是事务开始时间，锁等待会让后插入者持有更早的时间戳。
    """
    stmt = select(func.max(RequestDispatch.seq)).where(
        RequestDispatch.request_id == request_id
    )
    current = (await db.execute(stmt)).scalar()
    return (current or 0) + 1


async def _record_history(
    db: AsyncSession,
    *,
    request: Request,
    action: str,
    actor_id: str,
    from_assignee_id: str | None,
    to_assignee_id: str | None,
    reason: str | None,
) -> None:
    """append-only 经手链：只插入，不更新（同 004 触发历史纪律）。"""
    db.add(
        RequestDispatch(
            request_id=request.id,
            action=action,
            actor_id=actor_id,
            from_assignee_id=from_assignee_id,
            to_assignee_id=to_assignee_id,
            reason=reason,
            seq=await _next_seq(db, request.id),
        )
    )
    await db.flush()
    metrics.REQUEST_DISPATCH_ACTIONS.inc(action=action)


async def dispatch_request(
    db: AsyncSession,
    *,
    request_id: str,
    actor_id: str,
    target_user_id: str,
    note: str | None = None,
) -> Request:
    """把需求分派/转交给一名在职员工（责任转交，不触发执行）。

    - 首次分派（created → pending）与转交（pending|accepted → pending）
      走同一入口，由「当前有无 assignee」区分，历史里 action 不同；
    - 自派允许（单人闭环）；转交时旧接收人收到转交通知；
    - 执行中及终态需求 → 409（FR-005）。
    """
    request = await _load_request_locked(db, request_id)
    _check_access(request, actor_id)

    # 配额门（契约 §3 判定顺序第 3 项）：成本恒记 owner（R-03）——owner 额度
    # 耗尽时分派即拒，把错误暴露在「派人」这一刻，而不是让接收人 accept 后
    # 才在拖给 agent 时撞 429。user 分派不建 Run，此门是前置闸而非计费点。
    from app.services.quota_service import assert_user_quota

    await assert_user_quota(db, request.owner_id)

    if RequestStatus(request.status) not in DISPATCHABLE_STATUSES:
        raise AppError("CONFLICT", "需求已在执行或已终结，不能分派")

    target = await _load_active_user(db, target_user_id)

    # 020：组织范围门禁——「别让隔壁部门把活派过来」。
    # 放在目标存在性之后（404 先于 403，不暴露「这个人存不存在」之外的判断），
    # 放在状态迁移之前（失败不留任何副作用）。
    from app.services import org_service

    if not await org_service.can_assign_to(db, actor_id, target_user_id):
        raise AppError("FORBIDDEN", "你的部门无权把需求指派给该用户")

    action = "reassign" if request.assignee_id else "dispatch"
    previous_assignee = request.assignee_id

    # 状态迁移：accepted → pending（转交）；created → pending（首次）；
    # pending → pending 是状态机层面的 no-op（换人由本服务完成）。
    transition(request, RequestStatus.PENDING)
    request.assignee_id = target.id
    request.dispatched_at = _now()
    request.accepted_at = None  # 新接收人必须重新确认接收
    await db.flush()
    assert_dispatch_invariants(request)

    await _record_history(
        db,
        request=request,
        action=action,
        actor_id=actor_id,
        from_assignee_id=previous_assignee,
        to_assignee_id=target.id,
        reason=note,
    )

    # 通知：新接收人必收；转交时旧接收人（若非操作人本人）也收。
    notif_type = "request_reassigned" if action == "reassign" else "request_dispatched"
    await _notify(
        db,
        user_id=target.id,
        type_=notif_type,
        request=request,
        payload={"actor_id": actor_id, "note": note},
    )
    if action == "reassign" and previous_assignee and previous_assignee != actor_id:
        await _notify(
            db,
            user_id=previous_assignee,
            type_="request_reassigned",
            request=request,
            payload={"actor_id": actor_id, "to": target.id},
        )

    logger.info(
        "request_dispatched",
        request_id=request.id,
        action=action,
        actor_id=actor_id,
        to=target.id,
    )
    return request


async def accept_request(
    db: AsyncSession, *, request_id: str, actor_id: str
) -> Request:
    """当前接收人确认接收（pending → accepted）。

    仅当前 assignee 可接收——「员工接收」是责任确认动作，owner 代接收会让
    经手链失真（contracts §4）。owner 需要推进时可直接拖给 agent（3b）或退回。
    """
    request = await _load_request_locked(db, request_id)
    _check_access(request, actor_id)

    if request.assignee_id is None:
        raise AppError("CONFLICT", "需求尚未分派，无需接收")
    if request.assignee_id != actor_id:
        raise AppError("CONFLICT", "仅当前接收人可以接收")
    if request.status != RequestStatus.PENDING.value:
        raise AppError("CONFLICT", "需求不在待接收状态")

    transition(request, RequestStatus.ACCEPTED)
    request.accepted_at = _now()
    await db.flush()
    assert_dispatch_invariants(request)

    await _record_history(
        db,
        request=request,
        action="accept",
        actor_id=actor_id,
        from_assignee_id=None,
        to_assignee_id=actor_id,
        reason=None,
    )

    # 通知分派人（最近一次 dispatch/reassign 的操作人）；自派自接不通知自己。
    dispatcher_id = await _last_dispatcher_id(db, request_id)
    if dispatcher_id and dispatcher_id != actor_id:
        await _notify(
            db,
            user_id=dispatcher_id,
            type_="request_accepted",
            request=request,
            payload={"actor_id": actor_id},
        )

    logger.info("request_accepted", request_id=request.id, actor_id=actor_id)
    return request


async def reject_request(
    db: AsyncSession, *, request_id: str, actor_id: str, reason: str
) -> Request:
    """退回（pending|accepted → created）：清空责任指针，原因必填。

    创建者可撤回分派（派错了要能撤）；接收人可附原因退回。打回 created
    让 owner 重新决策，而不是打回「分派人手里」——经手链 A→B→C 时
    「回哪一环」会歧义（research R-04）。
    """
    request = await _load_request_locked(db, request_id)
    _check_access(request, actor_id)

    if request.status not in (
        RequestStatus.PENDING.value,
        RequestStatus.ACCEPTED.value,
    ):
        raise AppError("CONFLICT", "需求不在待接收/已接收状态，不能退回")

    previous_assignee = request.assignee_id
    transition(request, RequestStatus.CREATED)
    request.assignee_id = None
    request.dispatched_at = None
    request.accepted_at = None
    await db.flush()
    assert_dispatch_invariants(request)

    await _record_history(
        db,
        request=request,
        action="reject",
        actor_id=actor_id,
        from_assignee_id=previous_assignee,
        to_assignee_id=None,
        reason=reason,
    )

    # 通知分派人；若退回者就是分派人（owner 撤回），通知被撤的接收人。
    dispatcher_id = await _last_dispatcher_id(db, request_id)
    notify_target = dispatcher_id if dispatcher_id != actor_id else previous_assignee
    if notify_target and notify_target != actor_id:
        await _notify(
            db,
            user_id=notify_target,
            type_="request_rejected",
            request=request,
            payload={"actor_id": actor_id, "reason": reason},
        )

    logger.info(
        "request_rejected", request_id=request.id, actor_id=actor_id, reason=reason
    )
    return request


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)
