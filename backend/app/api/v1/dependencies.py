"""依赖边端点（009）：挂在需求下的有向「先后」结构 CRUD。

判定顺序（contracts §1）：认证 → 角色（viewer 403）→ 被依赖方归属 404
→ 自依赖 400 → 目标 404 → 重复 409 → 成环 409 → 落库 + 审计。

`dependency_title/status` 随响应返回：详情弹层的边列表一次拿全，
不对每个前置再发一次 /requests/{id}（那是 N+1）。

反向视图（`GET /requests/{id}/dependents`）挂在 requests.py——
它不属于 /dependencies 的子路径，语义是「谁在等我」。
"""

from fastapi import APIRouter, status

from app.api.deps import DB, CurrentUser, MemberUser, ResourceId
from app.core.audit import (
    REQUEST_DEPENDENCY_ADD,
    REQUEST_DEPENDENCY_REMOVE,
    write_audit,
)
from app.models.request import Request
from app.schemas.dependency import (
    DependencyEdgeCreate,
    DependencyEdgeList,
    DependencyEdgeOut,
)
from app.services import dependency_service

router = APIRouter(prefix="/requests/{request_id}/dependencies", tags=["dependencies"])


def _to_out(edge, target: Request) -> DependencyEdgeOut:
    return DependencyEdgeOut(
        id=edge.id,
        request_id=edge.request_id,
        dependency_id=edge.dependency_id,
        dependency_title=target.title,
        dependency_status=target.status,
        created_at=edge.created_at.isoformat(),
    )


@router.post("", response_model=DependencyEdgeOut, status_code=status.HTTP_201_CREATED)
async def add_dependency(
    request_id: ResourceId,
    payload: DependencyEdgeCreate,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """加边：B 依赖 A（{request_id}=B，body.dependency_id=A）。"""
    edge = await dependency_service.add_dependency(
        db,
        request_id=request_id,
        dependency_id=str(payload.dependency_id),
        user_id=user.id,
    )
    target = await db.get(Request, edge.dependency_id)
    assert target is not None  # add_dependency 已校验目标存在
    await write_audit(
        db,
        user_id=user.id,
        action=REQUEST_DEPENDENCY_ADD,
        target_type="request",
        target_id=request_id,
        detail={
            "dependency_id": edge.dependency_id,
            # 标题快照：边可删、需求可取消，事后排查不依赖目标还存在
            "dependency_title": target.title if target else None,
        },
        strict=True,
    )
    await db.commit()
    return _to_out(edge, target)


@router.get("", response_model=DependencyEdgeList)
async def list_dependencies(
    request_id: ResourceId, user: CurrentUser, _m: MemberUser, db: DB
):
    """我依赖谁（前置列表，带标题与状态）。"""
    await dependency_service._load_editable_request(db, request_id, user.id)
    pairs = await dependency_service.list_dependency_edges(db, request_id)
    items = [_to_out(edge, target) for edge, target in pairs]
    return {"items": items, "total": len(items)}


@router.delete("/{dependency_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_dependency(
    request_id: ResourceId,
    dependency_id: ResourceId,
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
):
    """删边（误连能改；被依赖方 owner-or-assignee 可操作，R-06）。"""
    # 先取标题快照再删——删边后目标可能紧接着被取消，快照是唯一留痕
    target = await db.get(Request, dependency_id)
    await dependency_service.remove_dependency(
        db, request_id=request_id, dependency_id=dependency_id, user_id=user.id
    )
    await write_audit(
        db,
        user_id=user.id,
        action=REQUEST_DEPENDENCY_REMOVE,
        target_type="request",
        target_id=request_id,
        detail={
            "dependency_id": dependency_id,
            "dependency_title": target.title if target else None,
        },
        strict=True,
    )
    await db.commit()
