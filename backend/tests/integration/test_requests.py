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
    resp = await client.post(
        REQUESTS_URL, headers=headers, json=_request_payload(**overrides)
    )
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
    await _create_request(
        client, headers, title="inventory sync", description="daily stock"
    )
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


# ---------- 运行结果构成（混合结果不再只是个「失败」） ----------


async def _add_run(
    db_session, request: dict, *, status: str, owner_id: str, index: int
) -> None:
    from app.models.run import Run

    db_session.add(
        Run(
            request_id=request["id"],
            owner_id=owner_id,
            assignee_type="agent",
            assignee_id=owner_id,
            strategy="fan_out",
            sequence=index,
            status=status,
        )
    )
    await db_session.commit()


async def test_request_detail_exposes_the_run_outcome_mix(
    client, make_headers, db_session
):
    """部分失败的需求仍判 failed，但构成要说清楚：3 完成 / 1 失败 / 1 取消。"""
    headers = await make_headers(role="member")
    request = await _create_request(client, headers)
    owner_id = request["owner_id"]
    for index, status in enumerate(
        ("completed", "completed", "completed", "failed", "cancelled")
    ):
        await _add_run(
            db_session, request, status=status, owner_id=owner_id, index=index
        )

    detail = await client.get(f"{REQUESTS_URL}/{request['id']}", headers=headers)
    assert detail.status_code == 200
    summary = detail.json()["runs_summary"]
    assert summary == {
        "total": 5,
        "completed": 3,
        "failed": 1,
        "cancelled": 1,
        "active": 0,
    }


async def test_list_requests_carries_the_same_summary(client, make_headers, db_session):
    """看板读的是列表，列表也必须带构成，否则详情弹窗只能显示一个红标。"""
    headers = await make_headers(role="member")
    request = await _create_request(client, headers)
    await _add_run(
        db_session, request, status="running", owner_id=request["owner_id"], index=0
    )

    items = (await client.get(REQUESTS_URL, headers=headers)).json()["items"]
    mine = next(item for item in items if item["id"] == request["id"])
    assert mine["runs_summary"] == {
        "total": 1,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "active": 1,
    }


async def test_an_unassigned_request_has_no_summary(client, make_headers):
    """还没分配的需求不显示 0/0 这种噪音。"""
    headers = await make_headers(role="member")
    request = await _create_request(client, headers)
    detail = await client.get(f"{REQUESTS_URL}/{request['id']}", headers=headers)
    assert detail.json()["runs_summary"] is None
