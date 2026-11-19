"""市场 listing 状态机的守卫单测（015 覆盖率补齐）。

listing 有 published / paused / rejected / revoked 四种状态，每次切换都有前置
条件。这些守卫写错的后果是**市场里出现不该出现的工具**：

- 未通过验证的版本被发布（`require_valid`）→ 用户装上一个必然失败的工具；
- 已撤销的 listing 再撤销一次 → 审计里出现两条互相矛盾的记录；
- 暂停一个没发布的 listing → 状态被改成 paused，看起来像「运营下架过」。

所以逐条锁住「什么情况下必须拒绝」比测「正常发布能成功」更有价值。
"""

import uuid

import pytest
from sqlalchemy import select

from app.api.errors import AppError
from app.models.external_tool import (
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
)
from app.services import marketplace_service

pytestmark = pytest.mark.integration


async def _server_version(
    db, user_id, *, availability="valid", server_status="published"
):
    server = ExternalToolServer(
        owner_id=user_id,
        created_by=user_id,
        name=f"Server {uuid.uuid4().hex[:6]}",
        description="fixture",
        endpoint_url="https://8.8.8.8/mcp",
        normalized_endpoint_url=f"https://8.8.8.8/{uuid.uuid4().hex[:6]}",
        maintainer="Platform",
        auth_mode="none",
        status=server_status,
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
        definition_hash=f"{uuid.uuid4().hex}",
        availability=availability,
    )
    db.add(version)
    await db.flush()
    return server, version


async def _listing(db, version, status="published"):
    listing = MarketplaceListing(
        tool_version_id=version.id,
        status=status,
        # usage_summary / data_handling 是 NOT NULL 且无默认值：市场条目必须
        # 对用户说明「这个工具拿什么数据、怎么处理」，不是可选装饰。
        usage_summary="查询已审核内容",
        data_handling="请求发送到已审核的服务器",
        restrictions={},
    )
    db.add(listing)
    await db.flush()
    return listing


def _code(exc: pytest.ExceptionInfo) -> str:
    return exc.value.code


# ---------- 审核：approve / reject ----------


async def test_review_rejects_short_reason(db_session, make_user):
    owner, _ = await make_user(role="admin")
    server, version = await _server_version(db_session, owner.id)

    with pytest.raises(AppError) as exc:
        await marketplace_service.approve_listing(
            db_session,
            server_id=server.id,
            tool_version_id=version.id,
            reviewer_id=owner.id,
            reason="ok",
        )
    assert _code(exc) == "INVALID_INPUT"


async def test_review_unknown_version_is_404(db_session, make_user):
    owner, _ = await make_user(role="admin")
    server, _version = await _server_version(db_session, owner.id)

    with pytest.raises(AppError) as exc:
        await marketplace_service.reject_listing(
            db_session,
            server_id=server.id,
            tool_version_id=str(uuid.uuid4()),
            reviewer_id=owner.id,
            reason="不符合规范",
        )
    assert _code(exc) == "NOT_FOUND"


async def test_approve_requires_valid_version(db_session, make_user):
    """未通过验证的版本不允许发布：否则用户装上一个必然失败的工具。"""
    owner, _ = await make_user(role="admin")
    server, version = await _server_version(
        db_session, owner.id, availability="invalid"
    )

    with pytest.raises(AppError) as exc:
        await marketplace_service.approve_listing(
            db_session,
            server_id=server.id,
            tool_version_id=version.id,
            reviewer_id=owner.id,
            reason="验证通过可以发布",
        )
    assert _code(exc) == "CONFLICT"


async def test_approve_requires_passed_server_validation(db_session, make_user):
    owner, _ = await make_user(role="admin")
    server, version = await _server_version(db_session, owner.id)
    server.current_validation_status = "failed"
    await db_session.flush()

    with pytest.raises(AppError) as exc:
        await marketplace_service.approve_listing(
            db_session,
            server_id=server.id,
            tool_version_id=version.id,
            reviewer_id=owner.id,
            reason="验证通过可以发布",
        )
    assert _code(exc) == "CONFLICT"


async def test_approve_publishes_listing(db_session, make_user):
    owner, _ = await make_user(role="admin")
    server, version = await _server_version(db_session, owner.id)
    listing = await _listing(db_session, version, status="pending_review")

    result = await marketplace_service.approve_listing(
        db_session,
        server_id=server.id,
        tool_version_id=version.id,
        reviewer_id=owner.id,
        reason="验证通过可以发布",
    )

    assert result["listing_id"] == listing.id
    refreshed = await db_session.get(MarketplaceListing, listing.id)
    assert refreshed.status == "published"
    assert refreshed.review_reason == "验证通过可以发布"


# ---------- 状态迁移：pause / resume / revoke ----------


async def test_transition_rejects_short_reason(db_session, make_user):
    owner, _ = await make_user(role="admin")
    _server, version = await _server_version(db_session, owner.id)
    listing = await _listing(db_session, version)

    with pytest.raises(AppError) as exc:
        await marketplace_service.transition_listing(
            db_session,
            listing_id=listing.id,
            reviewer_id=owner.id,
            action="pause",
            reason="x",
        )
    assert _code(exc) == "INVALID_INPUT"


async def test_pause_requires_published(db_session, make_user):
    owner, _ = await make_user(role="admin")
    _server, version = await _server_version(db_session, owner.id)
    listing = await _listing(db_session, version, status="paused")

    with pytest.raises(AppError) as exc:
        await marketplace_service.transition_listing(
            db_session,
            listing_id=listing.id,
            reviewer_id=owner.id,
            action="pause",
            reason="运营要求下架",
        )
    assert _code(exc) == "CONFLICT"


async def test_resume_requires_paused(db_session, make_user):
    owner, _ = await make_user(role="admin")
    _server, version = await _server_version(db_session, owner.id)
    listing = await _listing(db_session, version, status="published")

    with pytest.raises(AppError) as exc:
        await marketplace_service.transition_listing(
            db_session,
            listing_id=listing.id,
            reviewer_id=owner.id,
            action="resume",
            reason="恢复上架处理",
        )
    assert _code(exc) == "CONFLICT"


async def test_resume_blocked_when_server_unpublished(db_session, make_user):
    """恢复上架前要确认工具与服务器都还可用，否则上架一个装不上的工具。"""
    owner, _ = await make_user(role="admin")
    _unpublished_server, version = await _server_version(
        db_session, owner.id, server_status="paused"
    )
    listing = await _listing(db_session, version, status="paused")

    with pytest.raises(AppError) as exc:
        await marketplace_service.transition_listing(
            db_session,
            listing_id=listing.id,
            reviewer_id=owner.id,
            action="resume",
            reason="恢复上架处理",
        )
    assert _code(exc) == "CONFLICT"


async def test_revoke_marks_version_unavailable(db_session, make_user):
    owner, _ = await make_user(role="admin")
    _server, version = await _server_version(db_session, owner.id)
    listing = await _listing(db_session, version)

    await marketplace_service.transition_listing(
        db_session,
        listing_id=listing.id,
        reviewer_id=owner.id,
        action="revoke",
        reason="发现安全问题下架",
    )

    refreshed_version = await db_session.get(ExternalToolVersion, version.id)
    assert refreshed_version.availability == "revoked"


async def test_revoke_twice_conflicts(db_session, make_user):
    owner, _ = await make_user(role="admin")
    _server, version = await _server_version(db_session, owner.id)
    listing = await _listing(db_session, version, status="revoked")

    with pytest.raises(AppError) as exc:
        await marketplace_service.transition_listing(
            db_session,
            listing_id=listing.id,
            reviewer_id=owner.id,
            action="revoke",
            reason="重复撤销请求",
        )
    assert _code(exc) == "CONFLICT"


async def test_unknown_action_rejected(db_session, make_user):
    owner, _ = await make_user(role="admin")
    _server, version = await _server_version(db_session, owner.id)
    listing = await _listing(db_session, version)

    with pytest.raises(AppError) as exc:
        await marketplace_service.transition_listing(
            db_session,
            listing_id=listing.id,
            reviewer_id=owner.id,
            action="teleport",
            reason="不存在的动作测试",
        )
    assert _code(exc) == "INVALID_INPUT"


# ---------- 浏览与详情 ----------


async def test_listing_detail_hides_unpublished(db_session, make_user):
    owner, _ = await make_user(role="admin")
    _server, version = await _server_version(db_session, owner.id)
    listing = await _listing(db_session, version, status="paused")

    with pytest.raises(AppError) as exc:
        await marketplace_service.listing_detail(db_session, listing.id)
    assert _code(exc) == "NOT_FOUND"


async def test_browse_filters_narrow_results(db_session, make_user):
    """五个筛选条件各自生效：q / category / capability / maintainer / version。"""
    owner, _ = await make_user(role="admin")
    _server, version = await _server_version(db_session, owner.id)
    await _listing(db_session, version)
    await db_session.commit()

    async def _count(**kwargs) -> int:
        _rows, total = await marketplace_service.browse_listings(db_session, **kwargs)
        return total

    assert await _count() >= 1
    assert await _count(q="tool-") >= 1
    assert await _count(q="no-such-tool") == 0
    assert await _count(capability="knowledge.read") >= 1
    assert await _count(capability="not.granted") == 0
    assert await _count(maintainer="Platform") >= 1
    assert await _count(maintainer="Nobody") == 0
    assert await _count(version="1.0.0+digest") >= 1
    assert await _count(version="9.9.9") == 0
    assert await _count(category="nonexistent-category") == 0


async def test_browse_only_returns_published_valid(db_session, make_user):
    owner, _ = await make_user(role="admin")
    _good_server, good_version = await _server_version(db_session, owner.id)
    await _listing(db_session, good_version, status="published")
    _bad_server, bad_version = await _server_version(
        db_session, owner.id, availability="invalid"
    )
    await _listing(db_session, bad_version, status="published")
    await db_session.commit()

    rows, _total = await marketplace_service.browse_listings(db_session, page_size=200)

    ids = {row["listing_id"] for row in rows}
    hidden = (
        await db_session.execute(
            select(MarketplaceListing.id).where(
                MarketplaceListing.tool_version_id == bad_version.id
            )
        )
    ).scalar_one()
    assert hidden not in ids
