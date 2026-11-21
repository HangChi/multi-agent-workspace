"""项目端点（008）：需求的业务容器 CRUD。

判定顺序（contracts §1）：认证 → 角色（viewer 403）→ schema（422）→
同名（409）→ 落库 + 审计。删除的 detail 带 request_count——
「我的需求怎么集体没项目了」这类排查就靠这个数。
"""

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, MemberUser, ResourceId
from app.core.audit import (
    PROJECT_CREATE,
    PROJECT_DELETE,
    PROJECT_TRANSFER,
    PROJECT_UPDATE,
    write_audit,
)
from app.models.project_member import ProjectMember
from app.schemas.project import (
    ProjectCreate,
    ProjectList,
    ProjectOut,
    ProjectTransferIn,
    ProjectUpdate,
)
from app.services import project_member_service, project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, _m: MemberUser, user: CurrentUser, db: DB
):
    """建项目（同用户名下唯一，含已归档项目也占名字）。"""
    project = await project_service.create_project(db, user_id=user.id, payload=payload)
    await write_audit(
        db,
        user_id=user.id,
        action=PROJECT_CREATE,
        target_type="project",
        target_id=project.id,
        detail={"name": project.name},
        strict=True,
    )
    await db.commit()
    return ProjectOut.model_validate(project).model_copy(update={"request_count": 0})


@router.get("", response_model=ProjectList)
async def list_projects(
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
    scope: str = Query(
        default="mine",
        pattern="^(mine|relevant)$",
        description="mine=我创建的项目（管理页）；relevant=我创建的+派给我需求所属的（看板筛选下拉）",
    ),
    # 分页是可选的：不传 page 表示「要全量」——看板/表单的项目下拉靠它，
    # 少一个项目用户就没法把需求归过去。管理页传 page 才分页。
    page: int | None = Query(default=None, ge=1, description="页码；不传=全量"),
    page_size: int = Query(default=50, ge=1, le=100),
):
    """项目列表 + 每个项目的可见需求数（page 可选）。"""
    rows, total = await project_service.list_projects(
        db,
        user.id,
        scope=scope,
        limit=page_size if page is not None else None,
        offset=((page or 1) - 1) * page_size,
    )
    # 010：成员数一次批量（GROUP BY project_id），不逐项目查
    member_counts = (
        dict(
            (
                await db.execute(
                    select(ProjectMember.project_id, func.count())
                    .where(ProjectMember.project_id.in_([p.id for p, _ in rows]))
                    .group_by(ProjectMember.project_id)
                )
            ).all()
        )
        if rows
        else {}
    )
    return {
        "items": [
            ProjectOut.model_validate(p).model_copy(
                update={
                    "request_count": count,
                    # 含 owner（+1）——「这个项目有几个人」的自然口径
                    "member_count": member_counts.get(p.id, 0) + 1,
                }
            )
            for p, count in rows
        ],
        "total": total,
    }


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: ResourceId, user: CurrentUser, _m: MemberUser, db: DB
):
    """项目详情 + 可见需求数 + 成员数（010：owner ∪ 成员可读）。"""
    project, count, member_count = await project_service.get_project(
        db, project_id, user.id
    )
    return ProjectOut.model_validate(project).model_copy(
        update={"request_count": count, "member_count": member_count}
    )


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: ResourceId,
    payload: ProjectUpdate,
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
):
    """改项目：改名同事务同步需求的 project_name 快照。"""
    project = await project_service.update_project(
        db, project_id=project_id, user_id=user.id, payload=payload
    )
    await write_audit(
        db,
        user_id=user.id,
        action=PROJECT_UPDATE,
        target_type="project",
        target_id=project.id,
        detail={"fields": sorted(payload.model_dump(exclude_unset=True).keys())},
        strict=True,
    )
    await db.commit()
    _, count, member_count = await project_service.get_project(db, project.id, user.id)
    return ProjectOut.model_validate(project).model_copy(
        update={"request_count": count, "member_count": member_count}
    )


@router.post("/{project_id}/transfer", response_model=ProjectOut)
async def transfer_project(
    project_id: ResourceId,
    payload: ProjectTransferIn,
    user: CurrentUser,
    _m: MemberUser,
    db: DB,
):
    """移交项目所有权（017）：200 + ProjectOut（200 而非 201——没有新资源）。

    响应里的 request_count / member_count 用**新 owner** 的口径读：
    原 owner 在 keep_as_member=false 时已经不是参与者，用他的身份回读会 404。
    """
    new_owner_id = str(payload.new_owner_id)
    project = await project_member_service.transfer_ownership(
        db,
        project_id=project_id,
        user_id=user.id,
        new_owner_id=new_owner_id,
        keep_as_member=payload.keep_as_member,
    )
    await write_audit(
        db,
        user_id=user.id,
        action=PROJECT_TRANSFER,
        target_type="project",
        target_id=project_id,
        detail={
            "from_user_id": user.id,
            "to_user_id": new_owner_id,
            "keep_as_member": payload.keep_as_member,
            "project_name": project.name,
        },
        strict=True,
    )
    await db.commit()
    _, count, member_count = await project_service.get_project(
        db, project_id, new_owner_id
    )
    return ProjectOut.model_validate(project).model_copy(
        update={"request_count": count, "member_count": member_count}
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: ResourceId, user: CurrentUser, _m: MemberUser, db: DB
):
    """删项目：其下需求保留、归属清空（审计记 request_count）。"""
    affected = await project_service.delete_project(
        db, project_id=project_id, user_id=user.id
    )
    await write_audit(
        db,
        user_id=user.id,
        action=PROJECT_DELETE,
        target_type="project",
        target_id=project_id,
        detail={"request_count": affected},
        strict=True,
    )
    await db.commit()
