"""A failed egress health lease must stop untrusted code, not just its DNS."""

from unittest.mock import Mock

import pytest

from app.executors.egress_network import NetworkPolicyError
from app.executors.sandbox_client import SandboxInfraError, SandboxRun
from tests.sandbox_fixtures import FakeNetworkCapture

EXECUTION_ID = "6cefa4ec-039d-4f6d-8eae-bc9712d6fdb6"


def test_unhealthy_egress_prevents_user_process_start(fake_docker, monkeypatch):
    health = Mock(side_effect=NetworkPolicyError("mediator lease expired"))
    monkeypatch.setattr(FakeNetworkCapture, "assert_healthy", health, raising=False)
    with (
        pytest.raises(SandboxInfraError, match="出站.*不可用"),
        SandboxRun(
            execution_id=EXECUTION_ID, language="python", code="print(1)"
        ) as run,
    ):
        run.execute()
    assert health.call_count == 1
    assert not fake_docker.container.started
    assert fake_docker.container.removed == [True]
    assert all(volume.removed == 1 for volume in fake_docker.volumes.created)


def test_lost_egress_health_kills_running_user_process(fake_docker, monkeypatch):
    health = Mock(side_effect=[None, NetworkPolicyError("mediator stopped")])
    monkeypatch.setattr(FakeNetworkCapture, "assert_healthy", health, raising=False)
    with (
        pytest.raises(SandboxInfraError, match="出站.*不可用"),
        SandboxRun(
            execution_id=EXECUTION_ID, language="python", code="print(1)"
        ) as run,
    ):
        run.execute()
    assert health.call_count == 2
    assert fake_docker.container.started
    assert fake_docker.container.killed == ["SIGKILL"]
    assert fake_docker.container.removed == [True]


def test_cancellation_during_setup_never_starts_user_code(fake_docker):
    with SandboxRun(
        execution_id=EXECUTION_ID,
        language="python",
        code="print(1)",
        abort_check=lambda: True,
    ) as run:
        outcome = run.execute()
    assert outcome.cancelled
    assert outcome.exit_code is None
    assert not fake_docker.container.started
    assert fake_docker.container.removed == [True]
