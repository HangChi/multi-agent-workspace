"""需求服务（T044）：CRUD + 列表查询（状态/关键词/优先级/分页）+ 归属校验。

对齐 contracts/api.md 需求部分：
- GET /requests: ?status=&q=&priority=&page=&page_size=
- POST /requests: 创建（status=created）
- GET /requests/{id}: 详情（含 attachments）
"""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models.request import Request
from app.schemas.request import RequestCreate, RequestUpdate


def _check_owner(request: Request, user_id: str) -> None:
    if request.owner_id != user_id:
        raise AppError("FORBIDDEN", "无权操作他人需求")


async def create_request(
    db: AsyncSession, *, user_id: str, payload: RequestCreate
) -> Request:
    """创建需求（member/admin 已由路由层校验）。"""
    request = Request(
        owner_id=user_id,
        title=payload.title,
        description=payload.description,
        expected_output=payload.expected_output,
        priority=payload.priority.value,
    )
    db.add(request)
    await db.flush()
    return request


async def get_request(
    db: AsyncSession, request_id: str, user_id: str, *, for_update: bool = False
) -> Request:
    """取单个需求（归属校验）。

    Args:
        for_update: 加行级写锁（SELECT ... FOR UPDATE）。用于「读状态 → 改状态」
            必须原子的场景：分配时若不加锁，两个并发请求会同时读到 created，
            各自建一组 Run（同一需求被执行两次、成本翻倍）。
    """
    if for_update:
        stmt = select(Request).where(Request.id == request_id).with_for_update()
        request = (await db.execute(stmt)).scalars().first()
    else:
        request = await db.get(Request, request_id)
    if request is None:
        raise AppError("NOT_FOUND", "需求不存在")
    _check_owner(request, user_id)
    return request


async def list_requests(
    db: AsyncSession,
    *,
    user_id: str,
    status: str | None = None,
    q: str | None = None,
    priority: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Request], int]:
    """列表：仅自己的，支持状态/关键词/优先级过滤 + 分页。"""
    conditions = [Request.owner_id == user_id]
    if status:
        conditions.append(Request.status == status)
    if priority:
        conditions.append(Request.priority == priority)
    if q:
        like = f"%{q}%"
        conditions.append(
            or_(
                Request.title.ilike(like),
                Request.description.ilike(like),
                Request.expected_output.ilike(like),
            )
        )

    # 总数
    total = (
        await db.execute(
            select(func.count()).select_from(Request).where(*conditions)
        )
    ).scalar_one()

    # 分页列表（默认按创建时间倒序）
    stmt = (
        select(Request)
        .where(*conditions)
        .order_by(Request.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def update_request(
    db: AsyncSession, *, request_id: str, user_id: str, payload: RequestUpdate
) -> Request:
    """编辑需求（归属校验）。"""
    request = await get_request(db, request_id, user_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None and field in ("description", "expected_output"):
            setattr(request, field, None)
        elif value is not None:
            if field == "priority" and hasattr(value, "value"):
                value = value.value
            setattr(request, field, value)
    await db.flush()
    return request
