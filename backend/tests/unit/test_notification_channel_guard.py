"""018 通道的纯逻辑单测：签名向量、事件白名单、URL 守卫。

签名的期望值是**用 openssl 独立算出来的**（不是拿本实现算一遍再断言）：
`printf '1234567890.{"event":"test","title":"x"}' | openssl dgst -sha256 -hmac test-secret`
这样测试才能发现「实现和测试一起错」的情况。
"""

import pytest

from app.api.errors import AppError
from app.services.notification_channel_service import (
    SUBSCRIBABLE_EVENTS,
    _validate_events,
    _validate_url,
    sign,
)


def test_signature_matches_openssl_vector() -> None:
    body = b'{"event":"test","title":"x"}'
    assert (
        sign("test-secret", "1234567890", body)
        == "sha256=cf0e6cf5f5cf3696a78a210c4c7bacd8b04417e353839e309cd5d05fc369ca8c"
    )


def test_signature_changes_with_timestamp() -> None:
    """时间戳参与签名——这是「拒绝重放」的基础，不是装饰。"""
    body = b'{"a":1}'
    assert sign("s", "1", body) != sign("s", "2", body)


def test_signature_changes_with_body() -> None:
    assert sign("s", "1", b"a") != sign("s", "1", b"b")


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(AppError) as exc:
        _validate_events(["run_completed", "not_an_event"])
    assert exc.value.status_code == 400
    assert "not_an_event" in exc.value.message


def test_events_deduplicated_in_order() -> None:
    assert _validate_events(["run_completed", "run_completed", "dependency_ready"]) == [
        "run_completed",
        "dependency_ready",
    ]


def test_empty_events_means_all() -> None:
    assert _validate_events([]) == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://10.1.2.3/hook",
        "http://192.168.1.10/hook",
        "http://[::1]:9000/hook",
        "javascript:alert(1)",
        "file:///etc/passwd",
    ],
)
async def test_blocked_urls_are_rejected_on_save(url: str) -> None:
    """保存时就拒（FR-003）：用户当场知道配错了，而不是投递时才失败。"""
    with pytest.raises(AppError) as exc:
        await _validate_url(url)
    assert exc.value.status_code == 400


async def test_public_url_accepted(monkeypatch) -> None:
    """公网域名放行（解析结果被注入，不依赖测试机的真实 DNS）。"""
    from app.services import notification_channel_service as svc

    async def _fake_resolve(host: str) -> list[str]:
        assert host == "hooks.example.com"
        return ["93.184.216.34"]

    monkeypatch.setattr(svc, "resolve_public_hosts", _fake_resolve)
    assert (
        await _validate_url("https://hooks.example.com/aw")
        == "https://hooks.example.com/aw"
    )


async def test_allow_private_flag_permits_loopback(monkeypatch) -> None:
    """纯内网部署的开关：接收端就是内网网关时由运维开启（默认关）。"""
    from app.services import notification_channel_service as svc

    monkeypatch.setattr(svc.settings, "notification_allow_private_hosts", True)
    assert (
        await _validate_url("http://127.0.0.1:9999/hook")
        == "http://127.0.0.1:9999/hook"
    )


async def test_allow_private_flag_still_blocks_metadata(monkeypatch) -> None:
    """云元数据地址没有业务用途，任何开关都不该放开它。"""
    from app.services import notification_channel_service as svc

    monkeypatch.setattr(svc.settings, "notification_allow_private_hosts", True)
    with pytest.raises(AppError) as exc:
        await _validate_url("http://169.254.169.254/latest/meta-data")
    assert "元数据" in exc.value.message


def test_subscribable_events_cover_notification_enum() -> None:
    """可订阅列表必须与 notification_type 枚举一致（新增类型要同步这里）。

    这条守的是「新增了通知类型但忘了让它可订阅」——症状是用户勾不到，
    只能订阅「全部」。
    """
    from app.models.notification import Notification

    enum_values = set(Notification.__table__.c.type.type.enums)
    assert enum_values == set(SUBSCRIBABLE_EVENTS)
