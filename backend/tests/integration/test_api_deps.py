"""鉴权依赖的单测（015 覆盖率补齐）。

这些分支平时被各端点的集成测试顺带覆盖，但「顺带」意味着一旦某个端点没人测，
它的 401/403 语义就没人守。这里直接冲着依赖函数测：

- 所有「凭证不对」的情形必须是 **401**（未认证），不能漏成 500 或 403；
- 角色不符是 **403**（已认证但无权）；
- 资源不存在 **404**、不属于自己 **403**——两者的区别直接影响用户能不能
  从状态码推断出「这个 id 是否存在」。
"""

import uuid

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import (
    _resource_id,
    get_current_user,
    get_owned_resource,
    require_role,
)
from app.core.security import create_access_token, create_refresh_token
from app.models.agent import Agent
from app.models.user import User


def _creds(token: str, scheme: str = "Bearer") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme=scheme, credentials=token)


async def _user(db, *, status="active", role="member") -> User:
    user = User(
        email=f"deps-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status=status,
    )
    db.add(user)
    await db.commit()
    return user


async def test_missing_credentials_is_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await get_current_user(db_session, None)
    assert exc.value.status_code == 401


async def test_non_bearer_scheme_is_401(db_session):
    user = await _user(db_session)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            db_session, _creds(create_access_token(user.id, user.role), "Basic")
        )
    assert exc.value.status_code == 401


async def test_garbage_token_is_401(db_session):
    with pytest.raises(HTTPException) as exc:
        await get_current_user(db_session, _creds("not-a-jwt"))
    assert exc.value.status_code == 401


async def test_refresh_token_cannot_authenticate(db_session):
    """refresh token 只能换 access：拿它调业务接口必须被拒。"""
    user = await _user(db_session)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            db_session, _creds(create_refresh_token(user.id, user.role))
        )
    assert exc.value.status_code == 401


async def test_unknown_user_is_401(db_session):
    token = create_access_token(str(uuid.uuid4()), "member")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(db_session, _creds(token))
    assert exc.value.status_code == 401


async def test_disabled_user_is_401(db_session):
    """禁用账号即使 token 未过期也必须被挡住。"""
    user = await _user(db_session, status="disabled")
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            db_session, _creds(create_access_token(user.id, user.role))
        )
    assert exc.value.status_code == 401


async def test_valid_token_returns_user(db_session):
    user = await _user(db_session)
    resolved = await get_current_user(
        db_session, _creds(create_access_token(user.id, user.role))
    )
    assert resolved.id == user.id


async def test_require_role_blocks_wrong_role(db_session):
    user = await _user(db_session, role="viewer")
    checker = require_role("member", "admin")

    with pytest.raises(HTTPException) as exc:
        await checker(user)
    assert exc.value.status_code == 403


async def test_require_role_allows_matching_role(db_session):
    user = await _user(db_session, role="admin")
    checker = require_role("member", "admin")
    assert await checker(user) is user


async def _agent(db, owner: User) -> Agent:
    """归属校验要的是带 owner_id 的资源；User 自己没有 owner_id。"""
    agent = Agent(
        owner_id=owner.id,
        name=f"deps-agent-{uuid.uuid4().hex[:6]}",
        model="openai/gpt-4o-mini",
        system_prompt="x",
        tools=[],
    )
    db.add(agent)
    await db.commit()
    return agent


async def test_owned_resource_missing_is_404(db_session):
    user = await _user(db_session)
    with pytest.raises(HTTPException) as exc:
        await get_owned_resource(Agent, str(uuid.uuid4()), user, db_session)
    assert exc.value.status_code == 404


async def test_owned_resource_of_other_user_is_404(db_session):
    """016：非 owner 与「不存在」同响应（404 + 同一 detail），不再回 403。"""
    owner = await _user(db_session)
    stranger = await _user(db_session)
    agent = await _agent(db_session, owner)

    with pytest.raises(HTTPException) as foreign:
        await get_owned_resource(Agent, agent.id, stranger, db_session)
    with pytest.raises(HTTPException) as missing:
        await get_owned_resource(Agent, str(uuid.uuid4()), stranger, db_session)

    assert foreign.value.status_code == missing.value.status_code == 404
    assert foreign.value.detail == missing.value.detail


async def test_owned_resource_returns_row_for_owner(db_session):
    owner = await _user(db_session)
    agent = await _agent(db_session, owner)

    resolved = await get_owned_resource(Agent, agent.id, owner, db_session)
    assert resolved.id == agent.id


def test_resource_id_normalizes_valid_uuid():
    raw = "0F0E0D0C-0B0A-4908-8706-050403020100"
    assert _resource_id(raw) == raw.lower()


@pytest.mark.parametrize("bad", ["not-a-uuid", "123", "", "  "])
def test_resource_id_rejects_garbage_with_value_error(bad):
    """非法 id 必须是 422（ValueError 由 FastAPI 转），不能落到 500。"""
    with pytest.raises(ValueError):
        _resource_id(bad)
