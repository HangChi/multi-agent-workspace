"""Distributed per-server external invocation concurrency leases."""

import pytest

from app.services import external_concurrency


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set(self, key, value, *, nx, ex):
        assert nx is True
        assert ex > 0
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, _script, _key_count, key, value):
        if self.values.get(key) == value:
            del self.values[key]
            return 1
        return 0

    def close(self):
        pass


@pytest.mark.asyncio
async def test_external_server_leases_enforce_limit_and_release_only_owned_slot(
    monkeypatch,
):
    store = FakeRedis()
    monkeypatch.setattr(external_concurrency, "_redis_client", lambda: store)
    monkeypatch.setattr(
        external_concurrency.settings, "mcp_max_concurrency_per_server", 1
    )

    lease = await external_concurrency.acquire_slot(
        "https://tools.example.test/rpc", wait_seconds=0.05
    )
    assert lease is not None
    assert (
        await external_concurrency.acquire_slot(
            "https://tools.example.test/rpc", wait_seconds=0.02
        )
        is None
    )
    assert (
        await external_concurrency.acquire_slot(
            "https://other.example.test/rpc", wait_seconds=0.02
        )
        is not None
    )

    external_concurrency.release_slot("https://tools.example.test/rpc", "stale-token")
    assert (
        await external_concurrency.acquire_slot(
            "https://tools.example.test/rpc", wait_seconds=0.02
        )
        is None
    )

    external_concurrency.release_slot("https://tools.example.test/rpc", lease)
    replacement = await external_concurrency.acquire_slot(
        "https://tools.example.test/rpc", wait_seconds=0.02
    )
    assert replacement is not None


@pytest.mark.asyncio
async def test_external_server_slot_wait_is_abortable(monkeypatch):
    monkeypatch.setattr(external_concurrency, "_try_acquire", lambda *_args: None)
    checks = 0

    async def abort_check():
        nonlocal checks
        checks += 1
        return checks >= 2

    lease = await external_concurrency.acquire_slot(
        "https://tools.example.test/rpc",
        wait_seconds=1,
        abort_check=abort_check,
    )
    assert lease is None
