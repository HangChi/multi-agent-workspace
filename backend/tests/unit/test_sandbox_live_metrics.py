"""Docker discards cgroup metrics after exit: retain samples while running."""

import threading
from types import SimpleNamespace

from app.executors.sandbox_client import SandboxRun

EXECUTION_ID = "4cefa4ec-039d-4f6d-8eae-bc9712d6fdb6"


def test_live_resource_facts_are_not_lost_when_post_exit_stats_are_empty(fake_docker):
    original_create = fake_docker.containers.create
    sampled = threading.Event()

    def create(image, **kwargs):
        container = original_create(image, **kwargs)
        original_wait = container.wait

        def stats(*, stream=False, one_shot=False):
            assert not stream and one_shot, "metrics must not block for a second Docker sample"
            if container.status == "running":
                sampled.set()
                return {
                    "cpu_stats": {"cpu_usage": {"total_usage": 23_000_000}},
                    "memory_stats": {"usage": 4_000_000},
                }
            return {"cpu_stats": {"cpu_usage": {"total_usage": 0}}, "memory_stats": {}}

        def wait(timeout=None):
            assert sampled.wait(timeout=1), "sample the live cgroup before process exit"
            return original_wait(timeout)

        container.stats = stats
        container.wait = wait
        return container

    fake_docker.containers.create = create
    with SandboxRun(execution_id=EXECUTION_ID, language="python", code="print(1)") as run:
        outcome = run.execute()
    assert outcome.cpu_ms == 23
    assert outcome.mem_peak_bytes == 4_000_000


def test_empty_post_exit_cgroup_is_missing_not_measured_zero():
    run = SandboxRun(execution_id=EXECUTION_ID, language="python", code="print(1)")
    container = SimpleNamespace(stats=lambda **kwargs: {
        "cpu_stats": {"cpu_usage": {"total_usage": 0}},
        "memory_stats": {},
    })
    assert run._sample_stats(container) == (None, None)
