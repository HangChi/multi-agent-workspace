"""请求上下文中间件测试：X-Request-ID 透传/生成 + 访问日志串联。

价值：多请求并发时日志能按一次请求聚合（此前只能靠时间戳猜），
前端报错可带上 request_id 让后端精确定位。
"""

import pytest

REQ_ID_HEADER = "X-Request-ID"


async def test_generates_request_id_when_absent(client):
    resp = await client.get("/api/v1/agents")
    rid = resp.headers.get(REQ_ID_HEADER)
    assert rid
    assert len(rid) <= 64


async def test_echoes_client_request_id(client):
    resp = await client.get("/api/v1/agents", headers={REQ_ID_HEADER: "trace-abc-123"})
    assert resp.headers.get(REQ_ID_HEADER) == "trace-abc-123"


async def test_rejects_unsafe_request_id(client):
    """客户端传来的异常 request_id（换行注入/超长）不应原样进入日志。"""
    resp = await client.get(
        "/api/v1/agents", headers={REQ_ID_HEADER: "bad\nvalue" + "x" * 200}
    )
    rid = resp.headers.get(REQ_ID_HEADER, "")
    assert "\n" not in rid
    assert len(rid) <= 64
    assert rid != "bad\nvalue" + "x" * 200


async def test_access_log_contains_request_id(client, capsys):
    """访问日志带 request_id / 状态码 / 耗时（capsys 抓 stdout）。"""
    await client.get("/api/v1/agents", headers={REQ_ID_HEADER: "trace-log-777"})
    out = capsys.readouterr().out

    lines = [ln for ln in out.splitlines() if "request" in ln and "trace-log-777" in ln]
    assert lines, f"未找到带 request_id 的访问日志，实际输出：{out[:500]}"
    assert "duration_ms" in lines[0]
    assert "401" in lines[0]  # 无 token → 401


async def test_health_requests_are_quiet(client, capsys):
    """健康探针不写访问日志（每 10s 一次，会刷屏）。"""
    await client.get("/api/v1/health")
    out = capsys.readouterr().out
    assert "/api/v1/health" not in out


async def test_query_string_not_logged(client, capsys):
    """query 不进日志（WS 握手 token 走 query，落盘即泄露）。"""
    await client.get(
        "/api/v1/agents?token=super-secret-value",
        headers={REQ_ID_HEADER: "trace-qs-1"},
    )
    out = capsys.readouterr().out
    assert "super-secret-value" not in out


@pytest.mark.parametrize("path", ["/api/v1/health/ready"])
async def test_readiness_is_quiet(client, capsys, path: str):
    await client.get(path)
    out = capsys.readouterr().out
    assert "health/ready" not in out
