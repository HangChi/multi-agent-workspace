"""路径里的资源 id 必须是 UUID。

非法值在 SQLAlchemy/asyncpg 里是 `invalid input for query argument` 的 DBAPIError，
会穿透到兜底 500 并刷出完整堆栈；这是纯客户端错误，应是 4xx 且没有数据库往返。
"""


async def test_malformed_resource_id_is_a_client_error(client, make_headers):
    headers = await make_headers(role="member")
    cases = [
        ("GET", "/api/v1/runs/not-a-uuid"),
        ("GET", "/api/v1/groups/not-a-uuid"),
        ("GET", "/api/v1/requests/not-a-uuid"),
        ("PATCH", "/api/v1/agents/not-a-uuid"),
        ("GET", "/api/v1/runs/00000000/artifacts/also-bad"),
    ]
    for method, path in cases:
        response = await client.request(method, path, headers=headers, json={})
        assert response.status_code == 422, (
            method,
            path,
            response.status_code,
            response.text,
        )
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_well_formed_but_unknown_id_is_still_not_found(client, make_headers):
    """格式合法的未知 id 依旧按归属校验返回 404，不因校验而改变语义。"""
    import uuid

    headers = await make_headers(role="member")
    response = await client.get(f"/api/v1/runs/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
