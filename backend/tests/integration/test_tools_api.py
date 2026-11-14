"""工具目录 API 契约测试。"""

import pytest


@pytest.mark.asyncio
async def test_tools_requires_login(client):
    response = await client.get("/api/v1/tools")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_tools_matches_catalog(client, make_headers):
    headers = await make_headers(role="member")
    response = await client.get("/api/v1/tools", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert {item["name"] for item in body["items"]} == {
        "web_search",
        "read_url",
        "calculate",
        "run_python",
        "run_shell",
    }
    assert all(item["enabled"] for item in body["items"])
    assert all("param_schema" in item for item in body["items"])
