"""外部服务器登记/编辑的错误契约（015 覆盖率补齐）。

登记外部 MCP 服务器是管理员动作，几条守卫错了后果都很实际：

- **endpoint 唯一**：同一 endpoint 登记两次，验证任务会打两遍、凭据存两份，
  用户看到两个「可用/不可用」互相矛盾的条目；
- **改 endpoint 必须重置状态**：不改 status 的话，一个从没验证过的新地址会
  顶着上一次的 passed 状态被当成「已验证」；
- 非法 URL 要在入口拒绝（422），不能落库后由验证任务在后台失败。

用真实 HTTP 端点测，因为状态码与错误码本身就是契约的一部分。
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

SERVERS_URL = "/api/v1/external-tool-servers"


def _payload(**overrides) -> dict:
    body = {
        "name": "搜索服务",
        "description": "内部搜索",
        # 用公网 IP 而不是域名：登记时会校验「解析结果必须是公网地址」，
        # 沙箱里解析不了 example.com，会得到 400 而不是我们要测的分支。
        "endpoint_url": f"https://8.8.8.8/{uuid.uuid4().hex[:10]}",
        "maintainer": "平台组",
        "auth_mode": "none",
    }
    body.update(overrides)
    return body


async def test_create_server_requires_admin(client, make_headers):
    resp = await client.post(SERVERS_URL, json=_payload(), headers=await make_headers())
    assert resp.status_code == 403


async def test_create_server_success(client, make_headers):
    resp = await client.post(
        SERVERS_URL, json=_payload(), headers=await make_headers(role="admin")
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending_review"


async def test_duplicate_endpoint_conflicts(client, make_headers):
    """同一 endpoint 只能登记一次：否则验证任务打两遍、凭据存两份。"""
    headers = await make_headers(role="admin")
    body = _payload()
    assert (
        await client.post(SERVERS_URL, json=body, headers=headers)
    ).status_code == 201

    again = await client.post(
        SERVERS_URL,
        json={**body, "name": "重复登记"},
        headers=headers,
    )

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONFLICT"


async def test_invalid_endpoint_url_rejected_at_entry(client, make_headers):
    resp = await client.post(
        SERVERS_URL,
        json=_payload(endpoint_url="ftp://files.example.com/mcp"),
        headers=await make_headers(role="admin"),
    )
    assert resp.status_code == 422


async def test_create_with_credential_stores_it(client, make_headers):
    """带凭据登记：凭据要一并落库（加密），而不是丢弃等管理员再传一次。"""
    resp = await client.post(
        SERVERS_URL,
        json=_payload(
            auth_mode="static_bearer",
            credential={"label": "搜索 token", "kind": "bearer", "value": "s3cret"},
        ),
        headers=await make_headers(role="admin"),
    )
    assert resp.status_code == 201


async def test_update_endpoint_resets_validation_status(
    client, make_headers, db_session
):
    """换 endpoint 必须回到 draft：否则新地址会顶着旧地址的 passed 被当成已验证。"""
    from app.models.external_tool import ExternalToolServer

    headers = await make_headers(role="admin")
    created = await client.post(SERVERS_URL, json=_payload(), headers=headers)
    server_id = created.json()["id"]

    updated = await client.patch(
        f"{SERVERS_URL}/{server_id}",
        json={"endpoint_url": f"https://1.1.1.1/{uuid.uuid4().hex[:10]}"},
        headers=headers,
    )

    assert updated.status_code == 200
    assert updated.json()["status"] == "draft"
    row = await db_session.get(ExternalToolServer, server_id)
    assert row.current_validation_status is None


async def test_update_to_existing_endpoint_conflicts(client, make_headers):
    headers = await make_headers(role="admin")
    first = (await client.post(SERVERS_URL, json=_payload(), headers=headers)).json()
    second_body = _payload()
    second = (await client.post(SERVERS_URL, json=second_body, headers=headers)).json()

    resp = await client.patch(
        f"{SERVERS_URL}/{second['id']}",
        json={"endpoint_url": first["endpoint_url"]},
        headers=headers,
    )

    assert resp.status_code == 409


async def test_update_with_invalid_endpoint_rejected(client, make_headers):
    headers = await make_headers(role="admin")
    created = (await client.post(SERVERS_URL, json=_payload(), headers=headers)).json()

    resp = await client.patch(
        f"{SERVERS_URL}/{created['id']}",
        json={"endpoint_url": "not-a-url"},
        headers=headers,
    )

    # PATCH 的 endpoint 校验发生在服务层（validate_endpoint），因此是 400
    # INVALID_INPUT；创建时由 schema 校验，才是 422。
    assert resp.status_code == 400


async def test_list_filters_by_status_and_keyword(client, make_headers):
    headers = await make_headers(role="admin")
    body = _payload(name="按关键字筛选的服务")
    await client.post(SERVERS_URL, json=body, headers=headers)

    by_status = await client.get(
        SERVERS_URL,
        params={"status": "pending_review", "page_size": 100},
        headers=headers,
    )
    by_q = await client.get(SERVERS_URL, params={"q": "关键字筛选"}, headers=headers)
    none_match = await client.get(
        SERVERS_URL, params={"q": "绝对不存在的名字"}, headers=headers
    )

    assert by_status.status_code == 200
    assert any(item["id"] for item in by_status.json()["items"])
    assert [item["name"] for item in by_q.json()["items"]] == ["按关键字筛选的服务"]
    assert none_match.json()["items"] == []
