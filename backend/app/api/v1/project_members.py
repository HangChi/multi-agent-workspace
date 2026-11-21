"""项目成员端点（010）：挂在 `/projects/{id}/members`。

三级谓词（contracts §1.2）：owner 全权 / 成员可读列表 / 陌生人 404。
错误码分工：成员做 owner 动作 = 403；陌生人 = 404——两种「不够权」
不混码（006/007 立下、010 延续的纪律）。
"""

from fastapi import APIRouter, status

from app.api.deps import DB, CurrentUser, MemberUser, ResourceId
from app.core.audit import PROJECT_MEMBER_ADD, PROJECT_MEMBER_REMOVE, write_audit
from app.models.project import Project
from app.schemas.project_member import (
    ProjectMemberCreate,
    ProjectMemberList,
    ProjectMemberOut,
)
from app.services import project_member_service

router = APIRouter(prefix="/projects/{project_id}/members", tags=["project-members"])


@router.post("", response_model=ProjectMemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    project_id: ResourceId,
    payload: ProjectMemberCreate,
    _m: MemberUser,
    user: CurrentUser,
    db: DB,
):
    """加成员（owner 特权；被加者收到通知）。"""
    edge = await project_member_service.add_member(
        db,
        project_id=project_id,
        user_id=user.id,
        member_user_id=str(payload.user_id),
        role=payload.role,
    )
    project = await db.get(Project, project_id)
    await write_audit(
        db,
        user_id=user.id,
        action=PROJECT_MEMBER_ADD,
        target_type="project",
        target_id=project_id,
        detail={
            "member_user_id": edge.user_id,
            "project_name": project.name if project else None,
        },
        strict=True,
    )
    await db.commit()
    return ProjectMemberOut(
        user_id=edge.user_id,
        user_email=None,  # 响应不带邮箱：创建动作不需要展示对方邮箱
        is_owner=False,
        role=edge.role,
        joined_at=edge.created_at.isoformat(),
    )


@router.get("", response_model=ProjectMemberList)
async def list_members(
    project_id: ResourceId, user: CurrentUser, _m: MemberUser, db: DB
):
    """成员列表（owner + 成员；成员可见，陌生人 404）。"""
    rows = await project_member_service.list_members(db, project_id, user.id)
    return {
        "items": [
            ProjectMemberOut(
                user_id=uid,
                user_email=email,
                is_owner=is_owner,
                role=role,
                joined_at=joined_at,
            )
            for uid, email, is_owner, role, joined_at in rows
        ],
        "total": len(rows),
    }


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: ResourceId, user_id: ResourceId, user: CurrentUser, db: DB
):
    """移除成员（owner 特权；不能移除自己；被移出者收到通知）。"""
    await project_member_service.remove_member(
        db, project_id=project_id, user_id=user.id, member_user_id=user_id
    )
    project = await db.get(Project, project_id)
    await write_audit(
        db,
        user_id=user.id,
        action=PROJECT_MEMBER_REMOVE,
        target_type="project",
        target_id=project_id,
        detail={
            "member_user_id": user_id,
            "project_name": project.name if project else None,
        },
        strict=True,
    )
    await db.commit()
