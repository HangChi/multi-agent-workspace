"""指标渲染与跨进程合并的单测（015 覆盖率补齐）。

`/metrics` 是排障与告警的唯一数据源，而它的值来自「多个进程各自快照 → 合并 →
渲染」。这条链上每一步都可能安静地出错：

- 直方图合并如果只是覆盖而不是逐桶相加，面板上的分位数就会随实例数变化；
- 空序列不输出 0，面板会显示 no data（看起来像「没流量」而不是「就是 0」）；
- 发布失败必须返回 False 而不是抛——指标通道抖动不能让业务请求 500。

这些都写成了纯函数 / 可注入 fake 的形式，所以能直接测。
"""

import json

from app.core import metrics


class _FakeRedis:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.written: dict[str, tuple[str, int | None]] = {}

    def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("redis down")
        self.written[key] = (value, ex)
        return True


def _counter_snapshot(name, series):
    return {name: {"kind": "counter", "help": "h", "labelnames": [], "series": series}}


def _histogram_snapshot(name, series, buckets=(1.0, 5.0)):
    return {
        name: {
            "kind": "histogram",
            "help": "h",
            "labelnames": [],
            "buckets": list(buckets),
            "series": series,
        }
    }


def test_merge_sums_counters_across_processes():
    merged = metrics.merge_snapshots(
        [_counter_snapshot("m_a", {"{}": 2.0}), _counter_snapshot("m_a", {"{}": 3.0})]
    )
    assert merged["m_a"]["series"]["{}"] == 5.0


def test_merge_adds_histogram_buckets_sum_and_count():
    left = _histogram_snapshot(
        "m_h", {"{}": {"counts": [1, 2], "sum": 1.5, "count": 3}}
    )
    right = _histogram_snapshot(
        "m_h", {"{}": {"counts": [2, 2], "sum": 2.5, "count": 4}}
    )

    merged = metrics.merge_snapshots([left, right])

    series = merged["m_h"]["series"]["{}"]
    assert series["counts"] == [3, 4]
    assert series["sum"] == 4.0
    assert series["count"] == 7


def test_merge_copies_histogram_series_when_new():
    snapshot = _histogram_snapshot(
        "m_h2", {"{}": {"counts": [1, 2], "sum": 1.0, "count": 3}}
    )

    merged = metrics.merge_snapshots([snapshot])

    assert merged["m_h2"]["series"]["{}"] == {"counts": [1, 2], "sum": 1.0, "count": 3}


def test_merge_tolerates_shorter_bucket_arrays():
    """老进程用旧桶配置上报时，多出来的桶不能越界写。"""
    left = _histogram_snapshot("m_h3", {"{}": {"counts": [1], "sum": 1.0, "count": 1}})
    right = _histogram_snapshot(
        "m_h3", {"{}": {"counts": [5, 5, 5], "sum": 5.0, "count": 15}}
    )

    merged = metrics.merge_snapshots([left, right])

    assert merged["m_h3"]["series"]["{}"]["counts"][0] == 6


def test_render_empty_series_emits_zero():
    """没有样本也要输出 0：否则面板显示 no data，被误读成「没有流量」。"""
    text = metrics.render_merged(_counter_snapshot("m_zero", {}))

    assert "# HELP m_zero h" in text
    assert "# TYPE m_zero counter" in text
    assert "m_zero 0" in text


def test_render_histogram_emits_buckets_inf_sum_count():
    merged = _histogram_snapshot(
        "m_hist", {"{}": {"counts": [1, 3], "sum": 4.5, "count": 3}}, buckets=(1.0, 5.0)
    )

    text = metrics.render_merged(merged)

    assert 'm_hist_bucket{le="1"} 1' in text
    assert 'm_hist_bucket{le="5"} 3' in text
    assert 'm_hist_bucket{le="+Inf"} 3' in text
    # 无标签时花括号整个省略（Prometheus 文本格式约定）
    assert "m_hist_sum 4.5" in text
    assert "m_hist_count 3" in text


def test_render_escapes_label_values():
    snapshot = {
        "m_lab": {
            "kind": "counter",
            "help": 'he"lp\n',
            "labelnames": ["path"],
            "series": {json.dumps({"path": '/a"b'}): 1.0},
        }
    }

    text = metrics.render_merged(snapshot)

    assert 'path="/a\\"b"' in text


def test_publish_writes_snapshot_with_ttl():
    client = _FakeRedis()
    assert metrics.publish(client) is True

    ((key, (value, ex)),) = client.written.items()
    assert key.startswith(metrics.REMOTE_PREFIX)
    assert ex == metrics.REMOTE_TTL_SECONDS
    assert json.loads(value) == metrics.snapshot()


def test_publish_swallows_redis_failure():
    """指标发布失败不能影响业务：返回 False，绝不抛。"""
    assert metrics.publish(_FakeRedis(fail=True)) is False


def test_process_id_has_host_and_pid():
    pid = metrics.process_id()
    assert ":" in pid
    assert pid.split(":")[-1].isdigit()


def test_start_background_publisher_marks_thread_daemon(monkeypatch):
    """用 daemon 线程：否则 worker 退出时会被这个循环拖住。"""
    calls: list[int] = []

    def _fake_loop(client_factory):
        calls.append(1)

    monkeypatch.setattr(metrics, "_publisher_loop", _fake_loop)

    thread = metrics.start_background_publisher(lambda: _FakeRedis())
    thread.join(timeout=2)

    assert thread.daemon is True
    assert thread.name == "metrics-publisher"
    assert calls == [1]


class _StopLoop(Exception):
    pass


async def test_load_remote_snapshots_skips_self_and_garbage():
    """跨进程合并要排除自己（否则本进程指标算两遍）。"""

    class _AsyncRedis:
        def __init__(self, items):
            self.items = items

        async def scan_iter(self, match=None, count=None):
            for key in self.items:
                yield key

        async def get(self, key):
            return self.items[key]

    own_key = f"{metrics.REMOTE_PREFIX}{metrics.process_id()}"
    other_key = f"{metrics.REMOTE_PREFIX}other-host:999"
    client = _AsyncRedis(
        {
            own_key: b'{"a": {"kind": "counter"}}',
            other_key: b'{"b": {"kind": "counter"}}',
            f"{metrics.REMOTE_PREFIX}empty:1": b"",
            f"{metrics.REMOTE_PREFIX}bad:1": b"{not json",
        }
    )

    snapshots = await metrics.load_remote_snapshots(client)

    assert snapshots == [{"b": {"kind": "counter"}}]


async def test_load_remote_snapshots_degrades_on_redis_error():
    class _Broken:
        async def scan_iter(self, match=None, count=None):
            raise ConnectionError("redis down")
            yield  # pragma: no cover

    assert await metrics.load_remote_snapshots(_Broken()) == []


def test_publisher_loop_counts_failures_and_keeps_going(monkeypatch):
    """发布失败不能退出循环：指标断了不影响业务，但要继续尝试恢复。"""
    attempts: list[int] = []

    monkeypatch.setattr(metrics, "publish", lambda client: False)

    def _sleep(_seconds):
        attempts.append(1)
        if len(attempts) >= 3:
            raise _StopLoop

    monkeypatch.setattr(metrics.time, "sleep", _sleep)

    try:
        metrics._publisher_loop(lambda: object())
    except _StopLoop:
        pass

    assert len(attempts) == 3


def test_publisher_loop_tolerates_client_factory_errors(monkeypatch):
    """建连失败也算一次失败，不能把线程炸掉。"""
    attempts: list[int] = []

    def _factory():
        raise ConnectionError("cannot connect")

    def _sleep(_seconds):
        attempts.append(1)
        raise _StopLoop

    monkeypatch.setattr(metrics.time, "sleep", _sleep)

    try:
        metrics._publisher_loop(_factory)
    except _StopLoop:
        pass

    assert len(attempts) == 1
