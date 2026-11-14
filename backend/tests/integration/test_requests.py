"""需求集成测试（T041）：创建/详情/过滤/归属校验。

覆盖看板核心数据流：
- 创建 201（status=created，priority 默认 normal）
- 详情 200（含 attachments）
- 分页与 `?status=&q=&priority=` 过滤
- 归属校验 403 / 404
"""

REQUESTS_URL = "/api/v1/requests"


def _request_payload(**overrides: str) -> dict:
    payload = {
        "title": "test request",
        "description": "a test request",
        "expected_output": "expected result",
        "priority": "normal",
    }
    payload.update(overrides)
    return payload


async def _create_request(client, headers: dict, **overrides: str) -> dict:
    resp = await client.post(REQUESTS_URL, headers=headers, json=_request_payload(**overrides))
    assert resp.status_code == 201
    return resp.json()


# ---------- 创建 ----------


async def test_create_request_success(client, make_headers):
    """member 创建需求 → 201，status=created，priority 默认 normal。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers, title="build dashboard")
    assert req["id"]
    assert req["title"] == "build dashboard"
    assert req["status"] == "created"
    assert req["priority"] == "normal"
    assert req["attachments"] == []
    assert req["owner_id"]


async def test_create_request_priority_validation(client, make_headers):
    """非法 priority → 422（schema 校验）。"""
    headers = await make_headers(role="member")
    resp = await client.post(
        REQUESTS_URL,
        headers=headers,
        json=_request_payload(priority="urgent"),
    )
    assert resp.status_code == 422


async def test_create_request_empty_title(client, make_headers):
    """空标题 → 422。"""
    headers = await make_headers(role="member")
    resp = await client.post(
        REQUESTS_URL,
        headers=headers,
        json=_request_payload(title=""),
    )
    assert resp.status_code == 422


# ---------- 详情 ----------


async def test_get_request_detail(client, make_headers):
    """详情 → 200，含全部字段与空 attachments。"""
    headers = await make_headers(role="member")
    req = await _create_request(client, headers)
    resp = await client.get(f"{REQUESTS_URL}/{req['id']}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == req["id"]
    assert body["description"] == "a test request"
    assert body["expected_output"] == "expected result"
    assert body["attachments"] == []


async def test_get_request_not_found(client, make_headers):
    """不存在的需求 → 404。"""
    headers = await make_headers(role="member")
    resp = await client.get(
        f"{REQUESTS_URL}/00000000-0000-0000-0000-000000000000",
        headers=headers,
    )
    assert resp.status_code == 404


async def test_get_request_non_owner_forbidden(client, auth_headers):
    """非 owner 查看他人需求 → 403（归属校验）。"""
    owner_headers, _ = await auth_headers(role="member")
    req = await _create_request(client, owner_headers)

    other_headers, _ = await auth_headers(role="member")
    resp = await client.get(f"{REQUESTS_URL}/{req['id']}", headers=other_headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


# ---------- 列表与过滤 ----------


async def test_list_requests_own_only(client, auth_headers):
    """列表只返回自己的需求。"""
    headers_a, _ = await auth_headers(role="member")
    await _create_request(client, headers_a, title="mine")

    headers_b, _ = await auth_headers(role="member")
    await _create_request(client, headers_b, title="other")

    resp = await client.get(REQUESTS_URL, headers=headers_a)
    assert resp.status_code == 200
    titles = [item["title"] for item in resp.json()["items"]]
    assert "mine" in titles
    assert "other" not in titles


async def test_list_requests_status_filter(client, make_headers, db_session):
    """?status= 过滤（直接改 DB 状态模拟执行流转）。"""
    from app.models.request import Request

    headers = await make_headers(role="member")
    await _create_request(client, headers, title="created-one")
    req2 = await _create_request(client, headers, title="running-one")

    # 直接改 DB：req2 状态置 running（分配在 Phase 6 实现）
    db_req = await db_session.get(Request, req2["id"])
    db_req.status = "running"
    await db_session.commit()

    resp = await client.get(f"{REQUESTS_URL}?status=running", headers=headers)
    assert resp.status_code == 200
    titles = [item["title"] for item in resp.json()["items"]]
    assert titles == ["running-one"]

    resp = await client.get(f"{REQUESTS_URL}?status=created", headers=headers)
    titles = [item["title"] for item in resp.json()["items"]]
    assert titles == ["created-one"]


async def test_list_requests_priority_filter(client, make_headers):
    """?priority= 过滤。"""
    headers = await make_headers(role="member")
    await _create_request(client, headers, title="high-one", priority="high")
    await _create_request(client, headers, title="low-one", priority="low")

    resp = await client.get(f"{REQUESTS_URL}?priority=high", headers=headers)
    titles = [item["title"] for item in resp.json()["items"]]
    assert titles == ["high-one"]


async def test_list_requests_search(client, make_headers):
    """?q= 关键词过滤（标题/描述/期望产出）。"""
    headers = await make_headers(role="member")
    await _create_request(client, headers, title="payroll batch")
    await _create_request(client, headers, title="inventory sync", description="daily stock")
    await _create_request(client, headers, title="unrelated")

    resp = await client.get(f"{REQUESTS_URL}?q=stock", headers=headers)
    assert resp.status_code == 200
    titles = [item["title"] for item in resp.json()["items"]]
    assert titles == ["inventory sync"]


async def test_list_requests_pagination(client, make_headers):
    """分页：page/page_size 生效，total 正确。"""
    headers = await make_headers(role="member")
    for i in range(5):
        await _create_request(client, headers, title=f"req-{i}")

    resp = await client.get(f"{REQUESTS_URL}?page=1&page_size=2", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5

    resp = await client.get(f"{REQUESTS_URL}?page=3&page_size=2", headers=headers)
    assert len(resp.json()["items"]) == 1
