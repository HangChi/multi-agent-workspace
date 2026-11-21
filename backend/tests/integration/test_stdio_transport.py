"""024 stdio 传输接线：创建校验 + 校验服务的 transport 分叉。

不测「真实 npx 包」——用 `tests/fixtures/fake_mcp_stdio_server.py` 作为
被登记的 server，等价于一个真实 stdio MCP server（真实子进程）。
"""

import os
import sys
import uuid

import pytest

from app.executors.stdio_pool import pool_size
from app.models.external_tool import ExternalToolServer
from app.models.user import User
from app.services.external_validation_service import validate_server

pytestmark = pytest.mark.integration

URL = "/api/v1/external-tool-servers"
SERVER = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "fixtures", "fake_mcp_stdio_server.py"
    )
)


async def _admin(db) -> User:
    user = User(
        email=f"stdio-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role="admin",
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


def _payload(**overrides) -> dict:
    body = {
        "name": f"stdio服务器-{uuid.uuid4().hex[:5]}",
        "description": "本地 stdio MCP server",
        "transport": "stdio",
        "command": sys.executable,
        "args": [SERVER],
        "maintainer": "test",
        "auth_mode": "none",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------- 创建校验


async def test_create_stdio_server_fills_endpoint_placeholder(
    db_session, client, make_headers
) -> None:
    admin = await _admin(db_session)
    resp = await client.post(URL, json=_payload(), headers=await make_headers(admin))
    assert resp.status_code == 201, resp.text[:300]
    body = resp.json()
    assert body["transport"] == "stdio"
    # 占位 URL 由服务端补，前端不需要填
    assert body["endpoint_url"].startswith("stdio://")
    assert body["command"] == sys.executable


async def test_stdio_requires_command(db_session, client, make_headers) -> None:
    admin = await _admin(db_session)
    payload = _payload()
    payload.pop("command")
    resp = await client.post(URL, json=payload, headers=await make_headers(admin))
    assert resp.status_code == 422


async def test_stdio_rejects_non_whitelisted_command(
    db_session, client, make_headers
) -> None:
    admin = await _admin(db_session)
    resp = await client.post(
        URL, json=_payload(command="/bin/sh"), headers=await make_headers(admin)
    )
    assert resp.status_code == 422
    assert "白名单" in resp.text


async def test_stdio_rejects_http_credentials(db_session, client, make_headers) -> None:
    """stdio 没有 HTTP 头：配了凭证等于配了个永远用不上的东西。"""
    admin = await _admin(db_session)
    resp = await client.post(
        URL,
        json=_payload(
            auth_mode="static_bearer",
            credential={"label": "x", "kind": "bearer", "value": "secret"},
        ),
        headers=await make_headers(admin),
    )
    assert resp.status_code == 422


async def test_http_rejects_stdio_args(db_session, client, make_headers) -> None:
    """半配置是最难排查的状态：http 行带 command 直接拒。"""
    admin = await _admin(db_session)
    resp = await client.post(
        URL,
        json={
            "name": f"混用-{uuid.uuid4().hex[:5]}",
            "description": "x",
            "transport": "http",
            "endpoint_url": "https://example.com/mcp",
            "command": "npx",
            "maintainer": "t",
            "auth_mode": "none",
        },
        headers=await make_headers(admin),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------- 校验服务分叉


async def test_validate_server_discovers_stdio_tools(db_session) -> None:
    """校验服务对 stdio 行：跳过 URL 校验，启动子进程握手 + tools/list。"""
    admin = await _admin(db_session)
    server = ExternalToolServer(
        owner_id=admin.id,
        created_by=admin.id,
        name=f"stdio-{uuid.uuid4().hex[:5]}",
        description="fake",
        transport="stdio",
        command=sys.executable,
        args=[SERVER],
        env_keys=[],
        endpoint_url="stdio://fake",
        normalized_endpoint_url="stdio://fake",
        source_url=None,
        maintainer="t",
        auth_mode="none",
        status="pending_review",
    )
    db_session.add(server)
    await db_session.commit()

    record = await validate_server(db_session, server=server, requested_by=admin.id)
    assert record.connection_status == "passed", record.safe_error
    assert record.discovered_count == 1
    assert record.definition_digest
    # 校验用的是一次性进程，不该在池里留下常驻条目
    assert pool_size() == 0
