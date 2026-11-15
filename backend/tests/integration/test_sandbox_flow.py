"""002 acceptance: mock LLM → real _execute → Docker SDK fake → PostgreSQL/MinIO."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.executors.egress_network import EGRESS_DNS_PROXY, NET_IMAGE
from app.executors.llm import LLMResponse, ToolCall
from app.models.agent import Agent
from app.models.artifact import Artifact
from app.models.audit_log import AuditLog
from app.models.egress import EgressAccess
from app.models.run import Run
from app.models.sandbox_execution import SandboxExecution
from app.models.step import Step
from app.services import artifact_service
from app.tasks.execute_run import _execute
from tests.sandbox_fixtures import FakeDockerClient, make_tar_bytes


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def bucket_exists(self, _bucket: str) -> bool:
        return True

    def put_object(self, bucket, key, data, length, content_type=None):
        self.objects[(bucket, key)] = data.read() if hasattr(data, "read") else data

    def get_object(self, bucket, key):
        return io.BytesIO(self.objects[(bucket, key)])

    def remove_object(self, bucket, key):
        self.objects.pop((bucket, key), None)


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> FakeDockerClient:
    """Extend the SDK fake, not SandboxRun/NetworkCapture/application persistence."""
    fake = FakeDockerClient()
    fake.existing_images = {settings.sbx_image}
    fake.helper_commands = []
    fake.stdout = b""
    fake.output_files = {}
    dns_proxy = SimpleNamespace(
        attrs={"NetworkSettings": {"Networks": {}}},
        reload=lambda: None,
        logs=lambda **kwargs: b"",
    )
    create_network = fake.networks.create

    def network_create(name, **kwargs):
        network = create_network(name, **kwargs)
        network.attrs = {
            "Id": "0123456789abcdef",
            "Options": {"com.docker.network.bridge.name": "br-integration"},
        }

        def connect(proxy):
            proxy.attrs["NetworkSettings"]["Networks"][name] = {"IPAddress": "172.30.0.2"}

        network.connect = connect
        network.disconnect = lambda proxy, **kwargs: None
        return network

    create_container = fake.containers.create

    def container_create(image, **kwargs):
        container = create_container(image, **kwargs)
        container.stdout = fake.stdout
        container.archived = make_tar_bytes(fake.output_files)
        container.attrs["NetworkSettings"] = {
            "Networks": {kwargs["network"]: {"IPAddress": "172.30.0.3"}}
        }
        return container

    def get_container(name):
        assert name == EGRESS_DNS_PROXY
        return dns_proxy

    def run_helper(image, **kwargs):
        assert image == NET_IMAGE, "user code must use create/put_archive/start, never run"
        fake.helper_commands.append(kwargs)
        if kwargs.get("detach"):
            return SimpleNamespace(
                stop=lambda **kwargs: None,
                logs=lambda **kwargs: (
                    b"tcpdump: listening on br-integration\n" if kwargs.get("stderr") else
                    b"1763170000.123 IP 172.30.0.3.42222 > 203.0.113.44.443: Flags [S], length 0\n"
                ),
                remove=lambda **kwargs: None,
            )
        return b""

    monkeypatch.setattr(fake.networks, "create", network_create)
    monkeypatch.setattr(fake.containers, "create", container_create)
    monkeypatch.setattr(fake.containers, "get", get_container, raising=False)
    monkeypatch.setattr(fake.containers, "run", run_helper)
    monkeypatch.setattr("app.executors.sandbox_client.docker.from_env", lambda: fake)
    return fake


@pytest.fixture
def fake_object_store(monkeypatch: pytest.MonkeyPatch) -> MemoryObjectStore:
    """Fake only the MinIO SDK boundary; keep artifact_service.put_bytes real."""
    store = MemoryObjectStore()
    monkeypatch.setattr("app.services.artifact_service.Minio", lambda *args, **kwargs: store)
    return store


async def test_real_execute_persists_stdout_audit_and_artifact(
    client, make_headers, db_session, fake_docker, fake_object_store, monkeypatch
):
    """No fake runner/executor/service: only LLM transport and Docker SDK are replaced."""
    headers = await make_headers(role="member")
    code = "print('hello from container')\nopen('/workspace/output/report.txt', 'w').write('report')"
    report = b"artifact from sandbox"
    fake_docker.stdout = b"hello from container\n"
    fake_docker.output_files = {"output/report.txt": report}
    replies = iter(
        [
            LLMResponse(
                tool_calls=[ToolCall(name="run_python", arguments={"code": code, "timeout_s": 30})],
                cost_cents=2,
            ),
            LLMResponse(content="sandbox complete", cost_cents=3),
        ]
    )
    llm_messages: list[list[dict[str, Any]]] = []

    async def chat(_self, messages, **kwargs):
        llm_messages.append(json.loads(json.dumps(messages)))
        return next(replies)

    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", chat)
    agent_response = await client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": f"sandbox-{uuid.uuid4().hex[:8]}",
            "system_prompt": "run the requested code",
            "model": "gpt-4o-mini",
            "tools": ["run_python"],
            "max_steps": 5,
            "egress_mode": "restricted",
        },
    )
    assert agent_response.status_code == 201, agent_response.text
    rule_response = await client.post(
        f"/api/v1/agents/{agent_response.json()['id']}/egress-rules",
        headers=headers,
        json={"target_type": "cidr", "target": "203.0.113.0/24", "port_range": "443"},
    )
    assert rule_response.status_code == 201, rule_response.text
    request_response = await client.post(
        "/api/v1/requests",
        headers=headers,
        json={"title": "sandbox flow", "description": "print and emit a report", "priority": "normal"},
    )
    assert request_response.status_code == 201
    assigned = await client.post(
        f"/api/v1/requests/{request_response.json()['id']}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent_response.json()["id"]},
    )
    assert assigned.status_code == 202, assigned.text
    run = await db_session.get(Run, assigned.json()["run_id"])
    agent = await db_session.get(Agent, agent_response.json()["id"])
    assert run is not None and agent is not None

    await _execute(db_session, run, agent)
    await db_session.refresh(run)
    assert run.status == "completed", run.error
    assert run.result == {"answer": "sandbox complete"}
    assert run.cost_cents == 5
    execution = (
        await db_session.execute(select(SandboxExecution).where(SandboxExecution.run_id == run.id))
    ).scalar_one()
    assert execution.status == "ok", execution.error_message
    assert execution.stdout == "hello from container\n"
    assert execution.stdout_full_bytes == len(fake_docker.stdout)
    assert execution.code == code
    assert execution.code_sha256 == hashlib.sha256(code.encode()).hexdigest()
    assert execution.exit_code == 0
    assert execution.cpu_ms == 12
    assert execution.mem_peak_bytes == 3_000_000
    assert execution.duration_ms >= 0
    assert execution.finished_at is not None

    steps = (
        await db_session.execute(select(Step).where(Step.run_id == run.id).order_by(Step.seq))
    ).scalars().all()
    assert [step.kind for step in steps] == ["llm", "tool", "llm", "final"]
    assert [step.seq for step in steps] == [1, 2, 3, 4]
    assert steps[1].id == execution.step_id
    assert steps[1].status == "completed"
    assert "_sandbox_payload" not in steps[1].output
    tool_message = next(item for item in llm_messages[-1] if item["role"] == "tool")
    assert json.loads(tool_message["content"])["stdout"] == execution.stdout

    artifact = (
        await db_session.execute(select(Artifact).where(Artifact.run_id == run.id))
    ).scalar_one()
    assert artifact.filename == "report.txt"
    assert artifact.sha256 == hashlib.sha256(report).hexdigest()
    assert artifact.step_id == execution.step_id
    assert artifact.size_bytes == len(report)
    assert fake_object_store.objects[(settings.minio_bucket, artifact.object_key)] == report
    artifact_service._minio_client().remove_object(settings.minio_bucket, artifact.object_key)

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.target_type == "sandbox_execution",
                AuditLog.target_id == execution.id,
                AuditLog.action == "sandbox_exec",
            )
        )
    ).scalar_one()
    assert audit.user_id == run.owner_id
    assert audit.detail["status"] == "ok"
    assert audit.detail["exit_code"] == 0
    access = (
        await db_session.execute(
            select(EgressAccess).where(EgressAccess.execution_id == execution.id)
        )
    ).scalar_one()
    assert str(access.target_ip) == "203.0.113.44"
    assert access.port == 443
    assert access.action == "allow"
    assert access.target_domain == "ip-direct"
    assert fake_docker.container.removed == [True]
    assert fake_docker.network.removed == 1
    assert all(volume.removed == 1 for volume in fake_docker.volumes.created)
    assert any(command["command"][0] == "iptables" for command in fake_docker.helper_commands)
    script_tar = fake_docker.container.put_archives[0][1]
    with tarfile.open(fileobj=io.BytesIO(script_tar)) as archive:
        assert archive.extractfile("main.py").read().decode() == code
