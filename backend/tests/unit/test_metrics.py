"""指标注册表、多进程合并与 /metrics 端点。

为什么这些测试值得写：
- 文本格式错一个字符，Prometheus 整段丢弃且**报错在抓取侧**，应用侧毫无感知；
- 标签写错必须直接抛错，否则指标静默消失，面板空着没人知道；
- 路由标签若用真实路径，时间序列数会随 run_id 爆炸；
- 指标是进程内的：API 有 2 个 worker、Celery 是独立容器。**不做多进程合并，
  Worker 侧的 LLM/沙箱/终态指标在抓取端永远是 0** —— 这正是「配了告警但
  永不触发」的成因，必须有测试钉住。
"""

from __future__ import annotations

import asyncio
import json

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


# ---------- 基础类型 ----------


def test_counter_requires_declared_labels():
    with pytest.raises(ValueError):
        metrics.HTTP_REQUESTS.inc(method="GET", route="/x")  # 缺 status
    with pytest.raises(ValueError):
        metrics.HTTP_REQUESTS.inc(method="GET", route="/x", status="200", nope="1")


def test_counter_zero_series_when_empty():
    """没有样本也要输出 0，否则面板显示 no data 而不是 0。"""
    assert "aw_ws_connections_active 0" in metrics.render()


def test_gauge_inc_dec():
    metrics.WS_CONNECTIONS.inc()
    metrics.WS_CONNECTIONS.inc()
    metrics.WS_CONNECTIONS.dec()
    assert metrics.WS_CONNECTIONS.value() == 1.0


def test_histogram_buckets_are_cumulative():
    for value in (0.003, 0.02, 3.0):
        metrics.HTTP_DURATION.observe(value, method="GET", route="/api/v1/x")

    body = metrics.render()
    assert (
        'aw_http_request_duration_seconds_bucket{method="GET",route="/api/v1/x",le="0.005"} 1'
        in body
    )
    assert (
        'aw_http_request_duration_seconds_bucket{method="GET",route="/api/v1/x",le="0.025"} 2'
        in body
    )
    assert (
        'aw_http_request_duration_seconds_bucket{method="GET",route="/api/v1/x",le="+Inf"} 3'
        in body
    )
    assert (
        'aw_http_request_duration_seconds_count{method="GET",route="/api/v1/x"} 3'
        in body
    )


def test_label_values_are_escaped():
    counter = Counter("aw_test_escape_total", "escape check", ("value",))
    counter.inc(value='a"b\\c\nd')

    assert 'a\\"b\\\\c\\nd' in metrics.render()


def test_render_includes_help_and_type_for_all_metrics():
    body = metrics.render()
    for name in metrics.metric_names():
        assert f"# TYPE {name}" in body


def test_gauge_and_histogram_types_declared():
    body = metrics.render()
    assert "# TYPE aw_ws_connections_active gauge" in body
    assert "# TYPE aw_http_request_duration_seconds histogram" in body


# ---------- 多进程合并 ----------


def test_merge_sums_counters_and_histograms_across_processes():
    counter = Counter("aw_test_merge_total", "m", ("k",))
    histogram = Histogram("aw_test_merge_seconds", "h", ("k",), buckets=(0.1, 1.0))

    counter.inc(2, k="a")
    histogram.observe(0.05, k="a")
    snapshot_a = metrics.snapshot()

    metrics.reset()  # 模拟另一个进程从 0 开始
    counter.inc(3, k="a")
    histogram.observe(0.5, k="a")
    snapshot_b = metrics.snapshot()

    body = metrics.render_merged(metrics.merge_snapshots([snapshot_a, snapshot_b]))

    assert 'aw_test_merge_total{k="a"} 5.0' in body
    assert 'aw_test_merge_seconds_count{k="a"} 2' in body
    assert 'aw_test_merge_seconds_bucket{k="a",le="0.1"} 1' in body


def test_publish_writes_snapshot_with_ttl():
    class _FakeSyncRedis:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def set(self, key, value, ex=None):
            self.calls.append({"key": key, "value": value, "ex": ex})
            return True

    client = _FakeSyncRedis()
    metrics.LLM_CALLS.inc(model="gpt-4o-mini", outcome="ok")

    assert metrics.publish(client) is True
    call = client.calls[0]
    assert call["key"].startswith(metrics.REMOTE_PREFIX)
    assert call["ex"] == metrics.REMOTE_TTL_SECONDS
    assert json.loads(call["value"])["aw_llm_calls_total"]["series"]


def test_publish_failure_is_swallowed():
    class _Broken:
        def set(self, *args, **kwargs):
            raise RuntimeError("redis down")

    assert metrics.publish(_Broken()) is False


def test_load_remote_snapshots_skips_self():
    own_key = f"{metrics.REMOTE_PREFIX}{metrics.process_id()}"
    other = json.dumps(
        {"aw_x_total": {"kind": "counter", "help": "x", "labelnames": [], "series": {}}}
    )

    class _FakeAsyncRedis:
        async def scan_iter(self, match=None, count=None):
            for key in (own_key, "metrics:proc:other:1"):
                yield key

        async def get(self, key):
            return other if key != own_key else json.dumps({"self": {}})

    loaded = asyncio.run(metrics.load_remote_snapshots(_FakeAsyncRedis()))

    assert len(loaded) == 1
    assert "aw_x_total" in loaded[0]


# ---------- 端点 ----------


def test_metrics_endpoint_exposes_text_format():
    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE aw_run_terminal_total counter" in response.text
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


def test_metrics_endpoint_merges_worker_side_snapshots(monkeypatch):
    """Worker 侧的指标必须出现在 API 的 /metrics 里，否则告警永不触发。"""
    remote = {
        "aw_llm_calls_total": {
            "kind": "counter",
            "help": "LLM 调用次数（outcome=ok/error/timeout）",
            "labelnames": ["model", "outcome"],
            "series": {json.dumps({"model": "gpt-4o-mini", "outcome": "error"}): 7.0},
        }
    }

    async def fake_remote(_client):
        return [remote]

    monkeypatch.setattr(metrics, "load_remote_snapshots", fake_remote)

    body = TestClient(app).get("/metrics").text

    assert 'aw_llm_calls_total{model="gpt-4o-mini",outcome="error"} 7.0' in body


def test_http_metrics_use_route_template_not_raw_path():
    """真实路径做标签会让每个 id 一条序列 —— 必须用路由模板。"""
    client = TestClient(app)
    client.get("/api/v1/health")
    client.get("/api/v1/no-such-endpoint")  # 未匹配路由必须归一到一个标签值

    body = metrics.render()
    assert 'route="/api/v1/health"' in body
    assert 'route="unmatched"' in body


def test_run_terminal_counted_once_per_terminal_transition():
    from app.orchestrator.state_machine import RunState, RunStatus, transition

    run = RunState()
    transition(run, RunStatus.RUNNING)
    transition(run, RunStatus.COMPLETED)
    transition(run, RunStatus.COMPLETED)  # 幂等 no-op 不应重复计数

    assert metrics.RUN_TERMINAL.value(status="completed") == 1.0


def test_unused_gauge_type_still_available():
    assert Gauge("aw_test_gauge_only", "g").kind == "gauge"
