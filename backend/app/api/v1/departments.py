"""组织架构端点（020）。

读：member/admin 都能读（选人下拉要显示部门，普通成员也该看得懂组织）。
写：全部 admin（建/删部门、改分派范围、调整人员归属都是管理员动作）。

为什么不给部门负责人做「本部门管理」：先有负责人这个概念再说。020 只解决
「别让隔壁部门乱派人」，不做部门级自治。
"""

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentUser, MemberUser, ResourceId
from app.api.messages import raise_for_missing
from app.core.audit import ORG_DEPARTMENT_CREATE, ORG_DEPARTMENT_DELETE, write_audit
from app.models.department import Department
from app.models.user import User
from app.schemas.department import (
    DepartmentCreate,
    DepartmentList,
    DepartmentOut,
    DepartmentUpdate,
    UserDepartmentUpdate,
)
from app.services import org_service

router = APIRouter(prefix="/departments", tags=["departments"])


def _out(dept, member_count: int = 0) -> DepartmentOut:
    return DepartmentOut.model_validate(dept).model_copy(
        update={"member_count": member_count}
    )


@router.get("", response_model=DepartmentList)
async def list_departments(user: CurrentUser, _m: MemberUser, db: DB):
    """全部部门（按 path 排序 = 树的先序）+ 每部门成员数。"""
    rows = await org_service.list_tree(db)
    return {"items": [_out(d, n) for d, n in rows], "total": len(rows)}


@router.post("", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED)
async def create_department(payload: DepartmentCreate, admin: AdminUser, db: DB):
    dept = await org_service.create_department(
        db,
        name=payload.name,
        parent_id=str(payload.parent_id) if payload.parent_id else None,
        assign_scope=payload.assign_scope,
    )
    await write_audit(
        db,
        user_id=admin.id,
        action=ORG_DEPARTMENT_CREATE,
        target_type="department",
        target_id=dept.id,
        detail={
            "name": dept.name,
            "parent_id": dept.parent_id,
            "scope": dept.assign_scope,
        },
        strict=True,
    )
    await db.commit()
    return _out(dept)


@router.patch("/{department_id}", response_model=DepartmentOut)
async def update_department(
    department_id: ResourceId,
    payload: DepartmentUpdate,
    admin: AdminUser,
    db: DB,
):
    dept = await org_service.update_department(
        db,
        department_id=department_id,
        name=payload.name,
        assign_scope=payload.assign_scope,
        invite_quota=payload.invite_quota,
        set_invite_quota="invite_quota" in payload.model_fields_set,
    )
    await db.commit()
    return _out(dept)


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_department(department_id: ResourceId, admin: AdminUser, db: DB):
    """删部门（子树一并删除，成员变「未归属」）。"""
    dept = await db.get(Department, department_id)
    if dept is None:
        raise_for_missing("department")
    # 先取名字：删完就查不到了，而审计要留「删的是哪个部门」
    name = dept.name
    await write_audit(
        db,
        user_id=admin.id,
        action=ORG_DEPARTMENT_DELETE,
        target_type="department",
        target_id=department_id,
        detail={"name": name},
        strict=True,
    )
    await org_service.delete_department(db, department_id=department_id)
    await db.commit()


@router.put("/users/{user_id}/department", response_model=dict)
async def set_user_department(
    user_id: ResourceId,
    payload: UserDepartmentUpdate,
    admin: AdminUser,
    db: DB,
):
    """把用户放进/移出部门（admin）。"""
    user = await org_service.set_user_department(
        db,
        user_id=user_id,
        department_id=str(payload.department_id) if payload.department_id else None,
    )
    await db.commit()
    return {"user_id": user.id, "department_id": user.department_id}


@router.get("/members", response_model=dict)
async def list_department_members(user: CurrentUser, _m: MemberUser, db: DB):
    """用户 → 部门 的映射（前端管理页渲染「部门里的人」）。"""
    rows = (
        await db.execute(
            select(User.id, User.email, User.department_id).where(
                User.status == "active"
            )
        )
    ).all()
    return {
        "items": [
            {"user_id": uid, "email": email, "department_id": dept}
            for uid, email, dept in rows
        ]
    }
