"""Regression tests for bounded sandbox storage and live output capture."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.executors.sandbox_client import SandboxInfraError, SandboxRun
from tests.sandbox_fixtures import make_tar_bytes


def make_run() -> SandboxRun:
    return SandboxRun(execution_id="io-regression", language="python", code="print(1)")


def test_all_user_writable_working_paths_share_a_bounded_filesystem(fake_docker):
    with make_run() as run:
        run.execute()
        output = next(v for v in fake_docker.volumes.created if v.name == run.output_volume_name)
        options = output.created_kwargs["driver_opts"]
        assert options["type"] == options["device"] == "tmpfs"
        assert f"size={settings.sbx_disk_mb}m" in options["o"]
        binds = fake_docker.container.created_kwargs["volumes"]
        assert f"{output.name}:/workspace:rw" in binds
        assert f"{output.name}:/tmp:rw" in binds
        assert f"{output.name}:/dev/shm:rw" in binds
        assert f"{run.code_volume_name}:/code:ro" in binds


def test_archive_keeper_retains_only_execution_files_and_is_cleaned(fake_docker):
    run = make_run()
    run.execute()
    keeper = fake_docker.storage
    assert keeper.created_kwargs["network_mode"] == "none"
    assert keeper.created_kwargs["cap_drop"] == ["ALL"]
    assert keeper.created_kwargs["read_only"] is True
    assert keeper.put_archives[0][0] == "/code"
    keeper.archived = make_tar_bytes({"output/kept.txt": b"retained"})
    assert [(f.name, f.data) for f in run.iter_output_files()] == [("kept.txt", b"retained")]
    run.cleanup()
    assert keeper.removed == [True]
    assert all(v.removed == 1 for v in fake_docker.volumes.created)


def test_user_container_never_persists_unbounded_docker_logs(fake_docker):
    with make_run() as run:
        run.execute()
        assert fake_docker.container.created_kwargs["log_config"]["Type"] == "none"
        assert fake_docker.container.attach_calls == [
            {"stream": True, "stdout": True, "stderr": True, "logs": False, "demux": True}
        ]


def test_output_tail_counts_all_bytes_without_retaining_full_stream():
    from app.executors.sandbox_client import _OutputTail

    tail = _OutputTail(1024)
    for _ in range(10000):
        tail.add(b"x" * 1024)
        assert len(tail.data) <= 1024
    tail.add(b"TAIL")
    assert tail.snapshot() == (b"x" * 1020 + b"TAIL", True, 10240004)


def test_attach_failure_prevents_user_code_from_starting(fake_docker, monkeypatch):
    from docker.errors import APIError

    from tests.sandbox_fixtures import FakeContainer

    def fail_attach(self, **kwargs):
        raise APIError("capture unavailable")

    monkeypatch.setattr(FakeContainer, "attach", fail_attach, raising=False)
    with pytest.raises(SandboxInfraError, match="输出"):
        make_run().execute()
    assert not fake_docker.container.started
    assert all(c.removed == [True] for c in fake_docker.containers.created)


def test_storage_failure_does_not_fall_back_to_unbounded_volume(fake_docker, monkeypatch):
    from docker.errors import APIError

    original = fake_docker.volumes.create

    def fail_bounded_volume(name, **kwargs):
        if name.endswith("-out"):
            raise APIError("tmpfs unavailable")
        return original(name, **kwargs)

    monkeypatch.setattr(fake_docker.volumes, "create", fail_bounded_volume)
    with pytest.raises(SandboxInfraError):
        make_run().execute()
    # The network allocation lock is never started and is already released;
    # bounded-volume failure must still create neither keeper nor user container.
    assert all(c.created_kwargs["labels"].get("aw.role") == "ipam-lock"
               and not c.started and c.removed == [True] for c in fake_docker.containers.created)
    assert all(v.removed == 1 for v in fake_docker.volumes.created)
