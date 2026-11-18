"""用户端点（T024，007）：人员选择（分派落点）。

只读、最小投影（id/email/role）、仅在职。member/admin 可用；
viewer 403（与「创建需求」同权——不能建需求的人自然也不该选人分派）。
"""

from fastapi import APIRouter

from app.api.deps import DB, CurrentUser, MemberUser
from app.schemas.user import UserOption, UserOptionList
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/options", response_model=UserOptionList)
async def list_user_options(user: CurrentUser, _m: MemberUser, db: DB):
    """在职用户列表（分派选人用）。"""
    users = await user_service.list_options(db)
    return {
        "items": [UserOption.model_validate(u) for u in users],
        "total": len(users),
    }
