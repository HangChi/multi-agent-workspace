"""调度规则端点（T018，US1）。

对齐 contracts/api.md §1：

- `GET /schedules`            → 200 列表（member 仅自己，admin 全部）
- `POST /schedules`           → 201
- `GET /schedules/{id}`       → 200
- `PATCH /schedules/{id}`     → 200（admin 不可改他人）
- `DELETE /schedules/{id}`    → 204

审计：create/update/delete/enable/disable 各自一条（actions 见 core/audit.py）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import DB, CurrentUser, require_role
from app.core.audit import (
    SCHEDULE_CREATE,
    SCHEDULE_DELETE,
    SCHEDULE_DISABLE,
    SCHEDULE_ENABLE,
    SCHEDULE_UPDATE,
    write_audit,
)
from app.schemas.schedule import (
    CronPreviewIn,
    CronPreviewOut,
    ScheduleCreate,
    ScheduleList,
    ScheduleOut,
    ScheduleUpdate,
)
from app.services import schedule_service
from app.services.schedule_cron import (
    CronExpressionError,
    next_after,
    parse_cron,
    validate_cron,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])

MemberUser = Annotated[object, Depends(require_role("admin", "member"))]


@router.get("", response_model=ScheduleList)
async def list_schedules(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    enabled: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """规则列表：member 只看自己的，admin 看全部。viewer 403。"""
    items, total = await schedule_service.list_schedules(
        db, user=user, page=page, page_size=page_size, enabled=enabled
    )
    return ScheduleList(items=items, total=total)


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    payload: ScheduleCreate,
):
    out = await schedule_service.create_schedule(db, user=user, payload=payload)
    await write_audit(
        db,
        user_id=user.id,
        action=SCHEDULE_CREATE,
        target_type="schedule",
        target_id=out.id,
        detail={"name": out.name, "cron_expr": out.cron_expr},
    )
    await db.commit()
    return out


@router.post("/preview", response_model=CronPreviewOut)
async def preview_cron(
    user: CurrentUser,
    _m: MemberUser,
    payload: CronPreviewIn,
):
    """预览接下来几次触发时间（建规则时的实时提示）。

    用 200 返回解析失败而不是 4xx：这是"边输边提示"的场景，
    输入过程中的不完整表达式不是错误请求。
    """
    del user, _m  # 仅要求已登录且有 member 权限
    from datetime import UTC, datetime

    message = validate_cron(payload.cron_expr, payload.timezone, datetime.now(UTC))
    if message:
        return CronPreviewOut(error=message)

    try:
        cron = parse_cron(payload.cron_expr, payload.timezone)
        cursor = datetime.now(UTC)
        runs = []
        for _ in range(payload.count):
            cursor = next_after(cron, cursor)
            runs.append(cursor)
        return CronPreviewOut(next_runs=runs)
    except CronExpressionError as exc:
        return CronPreviewOut(error=str(exc))


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    schedule_id: str,
):
    return await schedule_service.get_schedule(db, user=user, schedule_id=schedule_id)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    schedule_id: str,
    payload: ScheduleUpdate,
):
    before = await schedule_service.get_schedule(
        db, user=user, schedule_id=schedule_id
    )
    out = await schedule_service.update_schedule(
        db, user=user, schedule_id=schedule_id, payload=payload
    )
    # 启停单独记一条：审计里"谁把它关了"比"改了什么字段"更重要
    action = SCHEDULE_UPDATE
    if before.enabled != out.enabled:
        action = SCHEDULE_ENABLE if out.enabled else SCHEDULE_DISABLE
    await write_audit(
        db,
        user_id=user.id,
        action=action,
        target_type="schedule",
        target_id=out.id,
        detail={"fields": sorted(payload.changes())},
    )
    await db.commit()
    return out


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    schedule_id: str,
) -> Response:
    await schedule_service.delete_schedule(db, user=user, schedule_id=schedule_id)
    await write_audit(
        db,
        user_id=user.id,
        action=SCHEDULE_DELETE,
        target_type="schedule",
        target_id=schedule_id,
    )
    await db.commit()
    return Response(status_code=204)
