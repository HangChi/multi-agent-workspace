"""API 依赖注入：JWT 用户解析、角色权限、资源归属校验。"""

from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import decode_token
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)

DB = Annotated[AsyncSession, Depends(get_db)]


def _unauthorized(detail: str = "未认证或 token 失效") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str = "无权限执行此操作") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def get_current_user(
    db: DB,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """解析 Bearer JWT，返回当前用户；无效则 401。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    token = credentials.credentials
    try:
        payload = decode_token(token)
    except JWTError:
        raise _unauthorized()
    if payload.get("type") != "access":
        raise _unauthorized()
    user_id = payload.get("user_id")
    if not user_id:
        raise _unauthorized()
    user = await db.get(User, user_id)
    if user is None or user.status != "active":
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: str):
    """角色权限依赖：当前用户角色不在允许列表内则 403。"""

    async def _checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise _forbidden()
        return user

    return _checker


async def get_owned_resource(
    model: type[Any], resource_id: str, user: CurrentUser, db: DB
) -> Any:
    """资源归属校验：资源必须存在且 owner 是当前用户，否则 404/403。"""
    resource: Any = await db.get(model, resource_id)
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资源不存在")
    if getattr(resource, "owner_id", None) != user.id:
        raise _forbidden()
    return resource
