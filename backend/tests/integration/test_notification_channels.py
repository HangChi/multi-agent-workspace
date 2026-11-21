"""018 通道端点与投递链路的集成测试。

两段：
- API 层（CRUD / 权限 / 404 口径 / URL 守卫）；
- 投递层（订阅过滤、禁用不建、200/500/400 三种结局）。

DNS 解析全部注入：测试机不该依赖外网，也不该因为「example.com 解析不到」
变成红。
"""

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.notification_channel import (
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_SUCCEEDED,
    NotificationChannel,
    NotificationDelivery,
)
from app.models.user import User
from app.services import notification_channel_service as svc

pytestmark = pytest.mark.integration

URL = "/api/v1/notification-channels"


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    """只去掉 DNS：域名当公网，字面 IP 仍走真实的内网判定。

    不要写成「一律返回公网」——那样 127.0.0.1 也会被放行，
    `test_create_with_private_url_400` 就变成一条永远测不到东西的假绿。
    """

    async def _fake(host: str) -> list[str]:
        import ipaddress

        from app.core.net_guard import NetGuardError, is_blocked_ip

        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return ["93.184.216.34"]
        if is_blocked_ip(str(ip)):
            raise NetGuardError(f"目标地址不允许访问（内网/保留地址）: {host}")
        return [str(ip)]

    monkeypatch.setattr(svc, "resolve_public_hosts", _fake)


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"nc-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _channel(db, user: User, *, url: str = "https://hooks.example.com/aw", **kw):
    channel = NotificationChannel(
        user_id=user.id,
        kind="webhook",
        url=url,
        secret="s" * 32,
        event_types=kw.pop("event_types", []),
        enabled=kw.pop("enabled", True),
    )
    db.add(channel)
    await db.commit()
    return channel


# ---------------------------------------------------------------- API 层


async def test_create_channel(db_session, client, make_headers) -> None:
    user = await _user(db_session)
    resp = await client.post(
        URL,
        json={
            "url": "https://hooks.example.com/abc",
            "event_types": ["run_completed"],
        },
        headers=await make_headers(user),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "webhook"
    assert body["event_types"] == ["run_completed"]
    assert body["enabled"] is True
    # secret 绝不回显，只告诉前端「已配置」
    assert body["secret_configured"] is True
    assert "secret" not in body


async def test_create_with_private_url_400(db_session, client, make_headers) -> None:
    user = await _user(db_session)
    resp = await client.post(
        URL,
        json={"url": "http://127.0.0.1:9000/hook"},
        headers=await make_headers(user),
    )
    assert resp.status_code == 400
    assert "不允许" in resp.json()["error"]["message"]


async def test_viewer_forbidden(db_session, client, make_headers) -> None:
    viewer = await _user(db_session, role="viewer")
    resp = await client.get(URL, headers=await make_headers(viewer))
    assert resp.status_code == 403


async def test_other_users_channel_is_404(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    other = await _user(db_session)
    channel = await _channel(db_session, owner)

    for method, path, kwargs in (
        ("patch", f"{URL}/{channel.id}", {"json": {"enabled": False}}),
        ("delete", f"{URL}/{channel.id}", {}),
        ("get", f"{URL}/{channel.id}/deliveries", {}),
    ):
        resp = await getattr(client, method)(
            path, headers=await make_headers(other), **kwargs
        )
        assert resp.status_code == 404, f"{method} 应 404，实得 {resp.status_code}"


async def test_toggle_and_delete(db_session, client, make_headers) -> None:
    user = await _user(db_session)
    headers = await make_headers(user)
    created = await client.post(
        URL, json={"url": "https://h.example.com/x"}, headers=headers
    )
    channel_id = created.json()["id"]

    off = await client.patch(
        f"{URL}/{channel_id}", json={"enabled": False}, headers=headers
    )
    assert off.status_code == 200 and off.json()["enabled"] is False

    deleted = await client.delete(f"{URL}/{channel_id}", headers=headers)
    assert deleted.status_code == 204
    listed = await client.get(URL, headers=headers)
    assert listed.json()["total"] == 0


# ---------------------------------------------------------------- 投递层


async def _notification(db, user: User, type_: str = "run_completed") -> Notification:
    note = Notification(user_id=user.id, type=type_, title="跑完了", payload={"k": "v"})
    db.add(note)
    await db.commit()
    return note


async def test_enqueue_only_subscribed_and_enabled(db_session) -> None:
    user = await _user(db_session)
    all_events = await _channel(db_session, user, url="https://a.example.com/1")
    only_dispatch = await _channel(
        db_session,
        user,
        url="https://b.example.com/2",
        event_types=["request_dispatched"],
    )
    disabled = await _channel(
        db_session, user, url="https://c.example.com/3", enabled=False
    )

    note = await _notification(db_session, user, type_="run_completed")
    delivery_ids = await svc.enqueue_for_notification(db_session, note)
    await db_session.commit()

    rows = (
        (
            await db_session.execute(
                select(NotificationDelivery).where(
                    NotificationDelivery.id.in_(delivery_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    assert {r.channel_id for r in rows} == {all_events.id}, (
        "只有「启用 + 订阅了该事件」的通道该建投递"
    )
    assert only_dispatch.id not in {r.channel_id for r in rows}
    assert disabled.id not in {r.channel_id for r in rows}
    assert rows[0].status == DELIVERY_PENDING


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_delivery_succeeds_on_200(db_session) -> None:
    user = await _user(db_session)
    channel = await _channel(db_session, user)
    note = await _notification(db_session, user)
    delivery = NotificationDelivery(
        channel_id=channel.id, notification_id=note.id, event_type=note.type
    )
    db_session.add(delivery)
    await db_session.commit()

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(200)

    outcome, _ = await svc.attempt_delivery(
        db_session, delivery.id, client=_client(handler)
    )
    await db_session.commit()

    assert outcome == svc.DeliveryOutcome.SUCCEEDED
    await db_session.refresh(delivery)
    assert delivery.status == DELIVERY_SUCCEEDED
    assert delivery.attempts == 1
    assert delivery.last_error is None
    # 头部契约：签名、时间戳、投递 id、事件类型
    assert captured["headers"]["x-aw-event"] == "run_completed"
    assert captured["headers"]["x-aw-delivery"] == delivery.id
    assert captured["headers"]["x-aw-signature"].startswith("sha256=")
    assert captured["headers"]["x-aw-timestamp"].isdigit()


async def test_delivery_retries_on_500(db_session) -> None:
    user = await _user(db_session)
    channel = await _channel(db_session, user)
    note = await _notification(db_session, user)
    delivery = NotificationDelivery(
        channel_id=channel.id, notification_id=note.id, event_type=note.type
    )
    db_session.add(delivery)
    await db_session.commit()

    outcome, backoff = await svc.attempt_delivery(
        db_session, delivery.id, client=_client(lambda _r: httpx.Response(500))
    )
    await db_session.commit()

    assert outcome == svc.DeliveryOutcome.RETRY
    assert backoff == 2
    await db_session.refresh(delivery)
    assert delivery.status == DELIVERY_PENDING
    assert delivery.attempts == 1
    assert delivery.last_error == "HTTP 500"


async def test_delivery_does_not_retry_4xx(db_session) -> None:
    """400/401/404 重试三次只是把同一个错误重复三遍（research R-05）。"""
    user = await _user(db_session)
    channel = await _channel(db_session, user)
    note = await _notification(db_session, user)
    delivery = NotificationDelivery(
        channel_id=channel.id, notification_id=note.id, event_type=note.type
    )
    db_session.add(delivery)
    await db_session.commit()

    outcome, _ = await svc.attempt_delivery(
        db_session, delivery.id, client=_client(lambda _r: httpx.Response(404))
    )
    await db_session.commit()

    assert outcome == svc.DeliveryOutcome.FAILED
    await db_session.refresh(delivery)
    assert delivery.status == DELIVERY_FAILED
    assert delivery.attempts == 1


async def test_delivery_gives_up_after_three_attempts(db_session) -> None:
    user = await _user(db_session)
    channel = await _channel(db_session, user)
    note = await _notification(db_session, user)
    delivery = NotificationDelivery(
        channel_id=channel.id,
        notification_id=note.id,
        event_type=note.type,
        attempts=2,
    )
    db_session.add(delivery)
    await db_session.commit()

    outcome, _ = await svc.attempt_delivery(
        db_session, delivery.id, client=_client(lambda _r: httpx.Response(503))
    )
    await db_session.commit()

    assert outcome == svc.DeliveryOutcome.FAILED
    await db_session.refresh(delivery)
    assert delivery.attempts == 3
    assert delivery.status == DELIVERY_FAILED


async def test_disabled_channel_drops_queued_delivery(db_session) -> None:
    """FR-008：排队期间被禁用 → 丢弃，不补发。"""
    user = await _user(db_session)
    channel = await _channel(db_session, user, enabled=False)
    note = await _notification(db_session, user)
    delivery = NotificationDelivery(
        channel_id=channel.id, notification_id=note.id, event_type=note.type
    )
    db_session.add(delivery)
    await db_session.commit()

    outcome, _ = await svc.attempt_delivery(
        db_session, delivery.id, client=_client(lambda _r: httpx.Response(200))
    )
    await db_session.commit()

    assert outcome == svc.DeliveryOutcome.FAILED
    await db_session.refresh(delivery)
    assert "禁用" in (delivery.last_error or "")


async def test_send_test_reports_status(db_session) -> None:
    user = await _user(db_session)
    channel = await _channel(db_session, user)

    ok = await svc.send_test(
        db_session,
        user_id=user.id,
        channel_id=channel.id,
        client=_client(lambda _r: httpx.Response(204)),
    )
    assert ok["ok"] is True and ok["status_code"] == 204

    bad = await svc.send_test(
        db_session,
        user_id=user.id,
        channel_id=channel.id,
        client=_client(lambda _r: httpx.Response(500)),
    )
    assert bad["ok"] is False and bad["error"] == "HTTP 500"
