"""健康检查测试：存活（liveness）与就绪（readiness）分离。

此前只有一个 /health 恒返回 ok：DB 挂了容器仍报 healthy，请求全 500，
编排层无法感知。readiness 逐项探测 DB / Redis 并在不可用时返回 503。
"""

import pytest

HEALTH_URL = "/api/v1/health"
READY_URL = "/api/v1/health/ready"


async def test_liveness_ok(client):
    """存活探针：进程能响应即 200。"""
    resp = await client.get(HEALTH_URL)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_readiness_ok(client):
    """就绪探针：测试环境 DB + Redis 均可用 → 200 + 逐项 ok。"""
    resp = await client.get(READY_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "redis": "ok"}


async def test_readiness_reports_db_failure(client, monkeypatch):
    """DB 不可用 → 503，且指明是 database 挂了（而不是整体失败无从定位）。"""
    from app import main as main_module

    class BrokenSession:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(main_module, "SessionLocal", BrokenSession())
    resp = await client.get(READY_URL)
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"].startswith("error")
    assert body["checks"]["redis"] == "ok"


async def test_readiness_reports_redis_failure(client, monkeypatch):
    """Redis 不可用 → 503，指明 redis 挂了。"""
    from app import main as main_module

    async def broken_ping() -> bool:
        raise ConnectionError("redis down")

    monkeypatch.setattr(main_module, "redis_ping", broken_ping)
    resp = await client.get(READY_URL)
    assert resp.status_code == 503
    assert resp.json()["checks"]["redis"].startswith("error")


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
async def test_docs_available_in_development(client, path: str):
    """非生产环境保留交互式文档（生产由 settings.is_production 关闭）。"""
    resp = await client.get(path)
    assert resp.status_code == 200
