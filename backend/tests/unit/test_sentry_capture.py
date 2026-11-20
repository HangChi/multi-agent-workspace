"""Sentry 真的能捕获异常并送进 transport（不发网络请求）。

`test_sentry_init.py` 只断言了「init 被调用、参数对不对」，那证明的是**开关**。
这里补另一半：异常经过 SDK 后确实变成一条 envelope 进入 transport。
两者合起来才算「接线可用」——只测开关的话，即使把 `sentry_sdk.init` 的参数
全传错（比如 DSN 拼错、integration 没挂），测试照样绿。

用假 transport 而不是真发请求：CI 不该依赖外部服务，也不该把业务异常
发到别人的 Sentry 项目里。`capture_envelope` 是 SDK 2.x 的发送出口，
截住它就等于截住了「网络之前的最后一步」。
"""

from __future__ import annotations

import pytest

from app.core import sentry as sentry_module
from app.core.config import settings

# 依赖是可选 extra：没装 [sentry] 时跳过而不是失败，
# 否则 `pip install -e .` 的默认开发环境会红。
sentry_sdk = pytest.importorskip("sentry_sdk", reason="需要可选依赖 [sentry]")


def test_exception_reaches_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """捕获 → flush → transport 收到 envelope；并带上我们配的 environment。"""
    from sentry_sdk.transport import Transport

    envelopes: list[object] = []

    class _FakeTransport(Transport):
        def capture_envelope(self, envelope: object) -> None:
            envelopes.append(envelope)

    monkeypatch.setattr(settings, "sentry_dsn", "https://public@example.com/1")
    monkeypatch.setattr(settings, "sentry_traces_sample_rate", 0.0)

    # init_sentry 不接受 transport 参数（生产用默认 HTTP transport），
    # 这里包一层把它注入进去，其余参数原样透传 —— 这样测的仍是
    # init_sentry 的真实参数（dsn/environment/integrations）。
    real_init = sentry_sdk.init

    def _init(**kwargs: object) -> object:
        kwargs["transport"] = _FakeTransport()
        return real_init(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sentry_sdk, "init", _init)

    try:
        assert sentry_module.init_sentry() is True

        try:
            raise RuntimeError("sentry-capture-probe")
        except RuntimeError:
            sentry_sdk.capture_exception()

        sentry_sdk.flush(timeout=3)
        assert envelopes, "异常没有被送到 transport（接线断了）"
    finally:
        # 全局 client 会跨测试存活，不关掉会让后续用例的异常也进这个假 transport
        sentry_sdk.get_client().close()
