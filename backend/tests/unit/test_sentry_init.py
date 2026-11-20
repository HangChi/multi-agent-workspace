"""`app.core.sentry.init_sentry` 的三条分支。

这个模块的价值全在「开关语义」上，所以测试也只断言开关：
DSN 为空必须完全不碰 SDK；DSN 有值但依赖缺失必须只警告不崩；
两者都在时才真正 init，且参数口径（采样率夹紧、PII 默认关）要传对。

不测「真的把异常发到了 Sentry」——那需要网络与账号，属于集成验证。
"""

from __future__ import annotations

import builtins
import sys
import types

import pytest

from app.core import sentry as sentry_module
from app.core.config import settings


def test_empty_dsn_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """DSN 为空：返回 False，且**不该**去 import sentry_sdk。

    断言「没 import」而不是只看返回值：这两件事在实现上是独立可错的，
    有人可能把 import 提到函数顶部，返回值照样是 False 但启动成本已经付了。
    """
    monkeypatch.setattr(settings, "sentry_dsn", "")

    imported: list[str] = []
    real_import = builtins.__import__

    def _spy(name: str, *args: object, **kwargs: object) -> object:
        imported.append(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _spy)

    assert sentry_module.init_sentry() is False
    assert [name for name in imported if name.startswith("sentry_sdk")] == []


def test_dsn_set_but_sdk_missing_warns_and_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DSN 有值但没装依赖：只警告并返回 False，绝不抛异常（否则监控挡住启动）。

    把 `sys.modules[name]` 设为 None 会让 import 直接抛 ImportError，
    比打桩更接近「真的没装这个包」。
    """
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@sentry.example.com/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)

    assert sentry_module.init_sentry() is False


def test_dsn_set_with_fake_sdk_passes_sanitized_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有 SDK 时：init 只调用一次，采样率被夹紧、PII 默认关、三个集成都挂上。"""
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@sentry.example.com/1")
    # 故意越界：SDK 自己会拒绝 5.0，实现必须把它夹到 1.0
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", 5.0)
    monkeypatch.setattr(settings, "sentry_send_default_pii", False)

    captured: dict[str, object] = {}
    fake_sdk = types.ModuleType("sentry_sdk")

    def _init(**kwargs: object) -> None:
        captured.update(kwargs)

    fake_sdk.init = _init  # type: ignore[attr-defined]

    def _integration_module(dotted: str, class_name: str) -> types.ModuleType:
        module = types.ModuleType(dotted)
        setattr(module, class_name, type(class_name, (), {}))
        return module

    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sdk)
    monkeypatch.setitem(
        sys.modules, "sentry_sdk.integrations", types.ModuleType("sentry_sdk.integrations")
    )
    for dotted, class_name in (
        ("sentry_sdk.integrations.celery", "CeleryIntegration"),
        ("sentry_sdk.integrations.fastapi", "FastApiIntegration"),
        ("sentry_sdk.integrations.sqlalchemy", "SqlalchemyIntegration"),
    ):
        monkeypatch.setitem(sys.modules, dotted, _integration_module(dotted, class_name))

    assert sentry_module.init_sentry() is True
    assert captured["dsn"] == "https://key@sentry.example.com/1"
    assert captured["traces_sample_rate"] == 1.0
    assert captured["send_default_pii"] is False
    integrations = captured["integrations"]
    assert isinstance(integrations, list) and len(integrations) == 3


def test_negative_sample_rate_is_clamped_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """负数采样率夹到 0，而不是让 SDK 抛 ValueError。"""
    monkeypatch.setattr(settings, "sentry_dsn", "https://key@sentry.example.com/1")
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", -3.0)

    captured: dict[str, object] = {}
    fake_sdk = types.ModuleType("sentry_sdk")
    fake_sdk.init = lambda **kwargs: captured.update(kwargs)  # type: ignore[attr-defined]

    def _integration_module(dotted: str, class_name: str) -> types.ModuleType:
        module = types.ModuleType(dotted)
        setattr(module, class_name, type(class_name, (), {}))
        return module

    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sdk)
    monkeypatch.setitem(
        sys.modules, "sentry_sdk.integrations", types.ModuleType("sentry_sdk.integrations")
    )
    for dotted, class_name in (
        ("sentry_sdk.integrations.celery", "CeleryIntegration"),
        ("sentry_sdk.integrations.fastapi", "FastApiIntegration"),
        ("sentry_sdk.integrations.sqlalchemy", "SqlalchemyIntegration"),
    ):
        monkeypatch.setitem(sys.modules, dotted, _integration_module(dotted, class_name))

    assert sentry_module.init_sentry() is True
    assert captured["traces_sample_rate"] == 0.0
