"""指标注册表与 /metrics 端点。

为什么这些测试值得写：
- 文本格式错一个字符，Prometheus 就整段丢弃且**报错在抓取侧**，应用侧毫无感知；
- 标签写错（没声明 / 漏传）必须直接抛错，否则指标静默消失，面板空着没人知道；
- 路由标签若用真实路径，时间序列数会随 run_id 爆炸——这是指标设计最常见的自伤。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import metrics
from app.core.metrics import Counter, Gauge, Histogram
from app.main import app


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


def test_counter_renders_with_help_and_type():
    text = metrics.HTTP_REQUESTS.render()
    assert "# HELP aw_http_requests_total" in text[0]
    assert text[1] == "# TYPE aw_http_requests_total counter"


def test_counter_requires_declared_labels():
    with pytest.raises(ValueError):
        metrics.HTTP_REQUESTS.inc(method="GET", route="/x")  # 缺 status
    with pytest.raises(ValueError):
        metrics.HTTP_REQUESTS.inc(method="GET", route="/x", status="200", nope="1")


def test_counter_zero_series_when_empty():
    """没有样本也要输出 0，否则面板显示 no data 而不是 0。"""
    assert metrics.WS_CONNECTIONS.render()[-1] == "aw_ws_connections_active 0"


def test_gauge_inc_dec():
    metrics.WS_CONNECTIONS.inc()
    metrics.WS_CONNECTIONS.inc()
    metrics.WS_CONNECTIONS.dec()
    assert metrics.WS_CONNECTIONS.value() == 1.0


def test_histogram_buckets_are_cumulative():
    for value in (0.003, 0.02, 3.0):
        metrics.HTTP_DURATION.observe(value, method="GET", route="/api/v1/x")

    text = "\n".join(metrics.HTTP_DURATION.render())
    assert (
        'aw_http_request_duration_seconds_bucket{method="GET",route="/api/v1/x",le="0.005"} 1'
        in text
    )
    assert (
        'aw_http_request_duration_seconds_bucket{method="GET",route="/api/v1/x",le="0.025"} 2'
        in text
    )
    assert (
        'aw_http_request_duration_seconds_bucket{method="GET",route="/api/v1/x",le="+Inf"} 3'
        in text
    )
    assert (
        'aw_http_request_duration_seconds_count{method="GET",route="/api/v1/x"} 3'
        in text
    )


def test_label_values_are_escaped():
    counter = Counter("aw_test_escape_total", "escape check", ("value",))
    counter.inc(value='a"b\\c\nd')
    line = counter.render()[-1]
    assert 'a\\"b\\\\c\\nd' in line


def test_render_returns_all_registered_metrics():
    body = metrics.render()
    for name in metrics.metric_names():
        assert f"# TYPE {name}" in body


def test_metrics_endpoint_exposes_text_format():
    client = TestClient(app)
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "aw_run_terminal_total" in response.text
    assert "aw_celery_queue_depth" in response.text


def test_metrics_endpoint_requires_token_when_configured(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "metrics_token", "s3cret")
    client = TestClient(app)

    assert client.get("/metrics").status_code == 401
    assert (
        client.get("/metrics", headers={"X-Metrics-Token": "s3cret"}).status_code == 200
    )


def test_metrics_endpoint_can_be_disabled(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "metrics_enabled", False)
    assert TestClient(app).get("/metrics").status_code == 404


def test_http_metrics_use_route_template_not_raw_path():
    """真实路径做标签会让每个 id 一条序列 —— 必须用路由模板。"""
    client = TestClient(app)
    client.get("/api/v1/health")
    client.get("/api/v1/no-such-endpoint")  # 未匹配路由必须归一到一个标签值

    body = metrics.render()
    assert 'route="/api/v1/health"' in body
    assert 'route="unmatched"' in body  # 未匹配路由归一到一个标签值


def test_run_terminal_counted_once_per_terminal_transition():
    from app.orchestrator.state_machine import RunState, RunStatus, transition

    run = RunState()
    transition(run, RunStatus.RUNNING)
    transition(run, RunStatus.COMPLETED)
    transition(run, RunStatus.COMPLETED)  # 幂等 no-op 不应重复计数

    assert metrics.RUN_TERMINAL.value(status="completed") == 1.0


def test_gauge_and_histogram_types_render():
    assert Gauge("aw_test_gauge", "g").render()[1] == "# TYPE aw_test_gauge gauge"
    assert Histogram("aw_test_hist", "h").render()[1] == "# TYPE aw_test_hist histogram"
