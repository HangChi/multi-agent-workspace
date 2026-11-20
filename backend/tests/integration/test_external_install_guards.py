"""市场安装/升级的守卫单测（015 覆盖率补齐）。

安装是「用户把第三方工具接到自己的 Agent 上」的动作，守卫写错的后果很具体：

- 装了未发布/已失效的 listing → 用户拿到一个必然失败的工具；
- 重复安装同一 listing → 产生两条安装记录，授权与配额都按两条算；
- 升级别人的安装 → 改的是别人的工具链；
- 升级到不可用的版本 → 静默把可用工具换成坏的。

幂等（重复安装返回已有记录）是刻意的：用户连点两次不该报错。
"""

import uuid

import pytest

from app.api.errors import AppError
from app.models.external_tool import (
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
)
from app.services import external_grant_service

pytestmark = pytest.mark.integration

MARKET_URL = "/api/v1/marketplace"


async def _published_listing(db, user_id, *, availability="valid", status="published"):
    server = ExternalToolServer(
        owner_id=user_id,
        created_by=user_id,
        name=f"Server {uuid.uuid4().hex[:6]}",
        description="fixture",
        endpoint_url="https://8.8.8.8/mcp",
        normalized_endpoint_url=f"https://8.8.8.8/{uuid.uuid4().hex[:8]}",
        maintainer="Platform",
        auth_mode="none",
        status="published",
        current_validation_status="passed",
    )
    db.add(server)
    await db.flush()
    version = ExternalToolVersion(
        server_id=server.id,
        external_name=f"tool-{uuid.uuid4().hex[:6]}",
        stable_name=f"mcp_server_{uuid.uuid4().hex[:8]}_v1",
        version="1.0.0+digest",
        description="desc",
        input_schema={"type": "object"},
        output_schema=None,
        required_permissions=["knowledge.read"],
        definition_hash=uuid.uuid4().hex,
        availability=availability,
    )
    db.add(version)
    await db.flush()
    listing = MarketplaceListing(
        tool_version_id=version.id,
        status=status,
        usage_summary="查询内容",
        data_handling="请求发往已审核服务器",
        restrictions={},
    )
    db.add(listing)
    await db.flush()
    return listing


async def test_install_requires_published_listing(db_session, make_user):
    owner, _ = await make_user()
    listing = await _published_listing(db_session, owner.id, status="paused")

    with pytest.raises(AppError) as exc:
        await external_grant_service.install_listing(
            db_session,
            listing_id=listing.id,
            owner_id=owner.id,
            installed_by=owner.id,
            version_policy="pinned",
        )
    assert exc.value.code == "NOT_FOUND"


async def test_install_rejects_invalid_version(db_session, make_user):
    owner, _ = await make_user()
    listing = await _published_listing(db_session, owner.id, availability="revoked")

    with pytest.raises(AppError) as exc:
        await external_grant_service.install_listing(
            db_session,
            listing_id=listing.id,
            owner_id=owner.id,
            installed_by=owner.id,
            version_policy="pinned",
        )
    assert exc.value.code == "NOT_FOUND"


async def test_install_is_idempotent(db_session, make_user):
    """重复安装返回已有记录：用户连点两次不该报错，也不该产生两条安装。"""
    owner, _ = await make_user()
    listing = await _published_listing(db_session, owner.id)

    first = await external_grant_service.install_listing(
        db_session,
        listing_id=listing.id,
        owner_id=owner.id,
        installed_by=owner.id,
        version_policy="pinned",
    )
    second = await external_grant_service.install_listing(
        db_session,
        listing_id=listing.id,
        owner_id=owner.id,
        installed_by=owner.id,
        version_policy="pinned",
    )

    assert first.id == second.id


async def test_list_installations_is_owner_scoped(db_session, make_user):
    owner, _ = await make_user()
    other, _ = await make_user()
    listing = await _published_listing(db_session, owner.id)
    await external_grant_service.install_listing(
        db_session,
        listing_id=listing.id,
        owner_id=owner.id,
        installed_by=owner.id,
        version_policy="pinned",
    )

    mine = await external_grant_service.list_installations(
        db_session, owner_id=owner.id
    )
    theirs = await external_grant_service.list_installations(
        db_session, owner_id=other.id
    )

    assert len(mine) == 1
    assert theirs == []


async def test_upgrade_rejects_foreign_installation(db_session, make_user):
    """升级别人的安装 → NOT_FOUND：与「安装不存在」同响应，不暴露安装存在（016）。"""
    owner, _ = await make_user()
    stranger, _ = await make_user()
    listing = await _published_listing(db_session, owner.id)
    installation = await external_grant_service.install_listing(
        db_session,
        listing_id=listing.id,
        owner_id=owner.id,
        installed_by=owner.id,
        version_policy="pinned",
    )
    target = await _published_listing(db_session, owner.id)

    with pytest.raises(AppError) as exc:
        await external_grant_service.upgrade_installation(
            db_session,
            installation_id=installation.id,
            new_listing_id=target.id,
            user_id=stranger.id,
            reason="尝试升级别人的工具",
        )
    assert exc.value.code == "NOT_FOUND"


async def test_installation_endpoints_round_trip(
    client, make_headers, make_user, db_session
):
    """HTTP 层：安装 → 列表里有它；重复安装仍然 201（幂等）。"""
    headers = await make_headers()
    user, _ = await make_user()
    listing = await _published_listing(db_session, user.id)
    await db_session.commit()

    first = await client.post(
        f"{MARKET_URL}/tools/{listing.id}/install",
        headers=headers,
        json={"version_policy": "pinned"},
    )
    second = await client.post(
        f"{MARKET_URL}/tools/{listing.id}/install",
        headers=headers,
        json={"version_policy": "pinned"},
    )
    listed = await client.get(f"{MARKET_URL}/installations", headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert any(item["id"] == first.json()["id"] for item in listed.json()["items"])


async def test_install_unpublished_listing_via_http(
    client, make_headers, make_user, db_session
):
    headers = await make_headers()
    user, _ = await make_user()
    listing = await _published_listing(db_session, user.id, status="paused")
    await db_session.commit()

    resp = await client.post(
        f"{MARKET_URL}/tools/{listing.id}/install",
        headers=headers,
        json={"version_policy": "pinned"},
    )

    assert resp.status_code == 404
