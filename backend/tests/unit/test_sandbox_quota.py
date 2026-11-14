"""沙箱并发槽位租约测试。"""

from app.services import sandbox_service


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.closed = False

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def eval(self, _script, _numkeys, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0

    def close(self):
        self.closed = True


def test_acquired_slot_is_released_on_context_exit(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sandbox_service.redis, "from_url", lambda *a, **k: fake)

    with sandbox_service.sandbox_slot() as acquired:
        assert acquired is True
        assert len(fake.values) == 1
    assert fake.values == {}


def test_busy_slots_return_false(monkeypatch):
    fake = FakeRedis()
    fake.values = {f"sbx:sem:{index}": "occupied" for index in range(4)}
    monkeypatch.setattr(sandbox_service.redis, "from_url", lambda *a, **k: fake)
    monkeypatch.setattr(sandbox_service.settings, "sbx_max_concurrency", 4)

    with sandbox_service.sandbox_slot() as acquired:
        assert acquired is False
    assert len(fake.values) == 4


def test_release_does_not_delete_another_lease(monkeypatch):
    fake = FakeRedis()
    fake.values["sbx:sem:0"] = "new-owner"
    monkeypatch.setattr(sandbox_service.redis, "from_url", lambda *a, **k: fake)
    sandbox_service.release_concurrency_slot("0:old-owner")
    assert fake.values["sbx:sem:0"] == "new-owner"
