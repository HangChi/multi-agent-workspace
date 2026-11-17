"""统一 500 错误处理测试。

此前未预期异常由 FastAPI 返回纯文本 "Internal Server Error"，前端
extractError 拿不到 message，用户只看到「请求失败，请稍后重试」，
排查时无法区分是校验失败还是服务崩了。
"""

import httpx
import pytest

from app.core.db import get_db
from app.main import app


@pytest.fixture
async def crash_client():
    """依赖注入直接抛异常 → 触发兜底 500，且不向上抛出（模拟生产行为）。"""

    async def broken_db():
        raise RuntimeError("simulated dependency crash")
        yield  # pragma: no cover - 不会执行

    app.dependency_overrides[get_db] = broken_db
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def test_unhandled_exception_returns_unified_json(crash_client):
    resp = await crash_client.get("/api/v1/requests")
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"]  # 有可展示文案，不是空


async def test_unhandled_exception_does_not_leak_details(crash_client):
    """内部异常细节不回传客户端（可能含路径/SQL/密钥片段）。"""
    resp = await crash_client.get("/api/v1/requests")
    text = resp.text
    assert "simulated dependency crash" not in text
    assert "RuntimeError" not in text
    assert "Traceback" not in text
