"""API 依赖注入：JWT 用户解析、角色权限、资源归属校验。"""

import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from pydantic import AfterValidator
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

def _resource_id(value: str) -> str:
    """资源 id 必须是 UUID：非法值应得到 4xx，而不是 SQL 层的类型错误 → 500。

    路由参数保持 str（服务层签名不变），只在这一处做格式校验。非法 UUID 在
    PostgreSQL 上是 `invalid input for query argument` 的 DBAPIError，会穿透
    到兜底 500 并在日志里刷完整堆栈；这是纯客户端错误。
    """
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("资源标识格式不正确") from None


ResourceId = Annotated[str, AfterValidator(_resource_id)]
