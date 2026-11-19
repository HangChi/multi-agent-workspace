"""统计快照缓存的单测（015 覆盖率补齐）。

`overview()` 在仪表盘端点上每几秒被调一次，`cache=True` 时走这个 15 秒有界
快照。缓存本身的失效与淘汰逻辑不涉及 SQL，纯函数，值得单独锁住——出问题时
的表现是「数据停在 15 秒前」或「内存随用户数增长」，都不容易从端到端看出来。
"""

import pytest

from app.services import stats_service as ss


@pytest.fixture(autouse=True)
def _clean_cache():
    ss._stats_cache.clear()
    yield
    ss._stats_cache.clear()


def test_copy_snapshot_copies_trend_list():
    """返回的 trend 必须是新列表：否则调用方改一下就把缓存内容改了。"""
    snapshot = {"a": 1, "cost_trend_7d": [1, 2, 3]}
    copied = ss._copy_snapshot(snapshot)

    assert copied["a"] == 1
    assert copied["cost_trend_7d"] == [1, 2, 3]
    assert copied["cost_trend_7d"] is not snapshot["cost_trend_7d"]


def test_cache_hit_within_ttl_returns_copy():
    ss._store_snapshot("u1", 100.0, {"cost_trend_7d": [1]})

    hit = ss._cached_snapshot("u1", 100.0 + ss._STATS_CACHE_TTL_SECONDS - 0.1)

    assert hit is not None
    assert hit["cost_trend_7d"] == [1]


def test_cache_miss_after_ttl_drops_entry():
    ss._store_snapshot("u1", 100.0, {"cost_trend_7d": [1]})

    assert ss._cached_snapshot("u1", 100.0 + ss._STATS_CACHE_TTL_SECONDS) is None
    # 过期即删除，避免「过期条目继续占内存直到被淘汰」
    assert "u1" not in ss._stats_cache


def test_cache_miss_for_unknown_owner():
    assert ss._cached_snapshot("nobody", 1.0) is None


def test_store_evicts_oldest_when_full(monkeypatch):
    """容量满时淘汰最久未写入的一条（不是随机、不是拒写）。"""
    monkeypatch.setattr(ss, "_STATS_CACHE_MAX_ENTRIES", 2)
    ss._store_snapshot("old", 1.0, {"cost_trend_7d": []})
    ss._store_snapshot("new", 2.0, {"cost_trend_7d": []})

    ss._store_snapshot("newest", 3.0, {"cost_trend_7d": []})

    assert set(ss._stats_cache) == {"new", "newest"}


def test_store_overwrites_existing_owner_without_eviction(monkeypatch):
    """同一个人重复写不触发淘汰：否则高频刷新会把别人的缓存挤掉。"""
    monkeypatch.setattr(ss, "_STATS_CACHE_MAX_ENTRIES", 2)
    ss._store_snapshot("a", 1.0, {"cost_trend_7d": []})
    ss._store_snapshot("b", 2.0, {"cost_trend_7d": []})

    ss._store_snapshot("a", 3.0, {"cost_trend_7d": [9]})

    assert set(ss._stats_cache) == {"a", "b"}
    assert ss._stats_cache["a"][1]["cost_trend_7d"] == [9]


def test_day_start_truncates_to_midnight():
    from datetime import UTC, datetime

    dt = datetime(2026, 11, 19, 18, 42, 3, tzinfo=UTC)
    assert ss._day_start(dt) == datetime(2026, 11, 19, 0, 0, 0, tzinfo=UTC)
