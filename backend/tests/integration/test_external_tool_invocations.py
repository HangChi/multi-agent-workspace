"""Invocation persistence and safe Run projection contract tests."""

import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from app.executors.llm import LLMResponse, ToolCall
from app.executors.mcp_client import MCPClient
from app.models.agent import Agent
from app.models.external_invocation import ExternalToolInvocation
from app.models.external_tool import (
    ExternalToolGrant,
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
    ToolInstallation,
)
from app.models.run import Run
from app.models.step import Step
from app.services import quota_service
from app.services.external_invocation_service import (
    list_run_invocations,
    record_invocation,
)
from app.tasks.execute_run import _execute

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from deploy.verify.mcp_fixture_server import handle_message

AGENTS_URL = "/api/v1/agents"
REQUESTS_URL = "/api/v1/requests"


async def _run_fixture(client, db_session, headers, owner):
    request_response = await client.post(
        "/api/v1/requests",
        headers=headers,
        json={"title": "external invocation", "description": "safe projection", "priority": "normal"},
    )
    assert request_response.status_code == 201
    server = ExternalToolServer(
        owner_id=owner.id,
        created_by=owner.id,
        name="Projection Server",
        description="fixture",
        endpoint_url="https://8.8.8.8/invocations",
        normalized_endpoint_url="https://8.8.8.8/invocations",
        maintainer="Platform",
        auth_mode="none",
        status="published",
        current_validation_status="passed",
    )
    db_session.add(server)
    await db_session.flush()
    version = ExternalToolVersion(
        server_id=server.id,
        external_name="lookup",
        stable_name="mcp_projection_lookup_v1",
        version="1.0.0+invocation",
        description="Lookup",
        input_schema={"type": "object"},
        required_permissions=["records.read"],
        definition_hash="invocation-digest",
        availability="valid",
    )
    db_session.add(version)
    await db_session.flush()
    run = Run(
        request_id=request_response.json()["id"],
        owner_id=owner.id,
        assignee_type="agent",
        assignee_id="00000000-0000-0000-0000-000000000001",
        strategy="direct",
        status="failed",
    )
    db_session.add(run)
    await db_session.flush()
    return run, server, version


@pytest.mark.asyncio
async def test_run_projection_contains_bounded_external_invocation_metadata_without_raw_values(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    run, server, version = await _run_fixture(client, db_session, headers, owner)
    db_session.add(
        ExternalToolInvocation(
            run_id=run.id,
            owner_id=owner.id,
            server_id=server.id,
            tool_version_id=version.id,
            call_id="call-1",
            status="auth_error",
            duration_ms=42,
            input_summary={"keys": ["query"]},
            output_summary=None,
            policy_result={"allowed": True},
            error_summary="外部工具服务器认证失败",
        )
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/runs/{run.id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["external_tool_invocations"]) == 1
    projection = body["external_tool_invocations"][0]
    assert projection["server_name"] == "Projection Server"
    assert projection["stable_name"] == version.stable_name
    assert projection["version"] == version.version
    assert projection["status"] == "auth_error"
    assert "ciphertext" not in response.text
    assert "query-value" not in response.text


@pytest.mark.asyncio
async def test_run_polling_selects_only_external_invocation_summary_columns(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    run, server, version = await _run_fixture(client, db_session, headers, owner)
    version.input_schema = {"type": "object", "description": "large-schema" * 4096}
    db_session.add(ExternalToolInvocation(
        run_id=run.id, owner_id=owner.id, server_id=server.id, tool_version_id=version.id,
        call_id="summary-only", status="succeeded", duration_ms=12,
        input_summary={"keys": ["query"]}, output_summary={"text": "large-result" * 4096},
        policy_result={"allowed": True},
    ))
    await db_session.commit()
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", capture)
    try:
        response = await client.get(f"/api/v1/runs/{run.id}", headers=headers)
    finally:
        event.remove(Engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    projection, = response.json()["external_tool_invocations"]
    assert projection["server_name"] == server.name
    assert projection["stable_name"] == version.stable_name
    assert projection["version"] == version.version
    assert projection["duration_ms"] == 12
    assert projection["error_category"] is None
    queries = [sql.split("FROM", 1)[0] for sql in statements
               if sql.lstrip().startswith("SELECT") and "FROM external_tool_invocations" in sql]
    assert len(queries) == 1
    for excluded in (".input_summary", ".output_summary", ".policy_result", ".error_summary",
                     ".input_schema", ".required_permissions", ".endpoint_url", ".description"):
        assert excluded not in queries[0]


@pytest.mark.asyncio
async def test_invocation_records_all_safe_error_categories_without_raw_values(
    client, db_session, make_headers, make_user
):
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    run, server, version = await _run_fixture(client, db_session, headers, owner)
    categories = [
        "succeeded",
        "parameter_error",
        "policy_denied",
        "transport_error",
        "auth_error",
        "timeout",
        "tool_error",
        "result_truncated",
        "cancelled",
    ]
    for index, category in enumerate(categories):
        await record_invocation(
            db_session,
            run_id=run.id,
            owner_id=owner.id,
            server_id=server.id,
            tool_version_id=version.id,
            grant_id=None,
            credential_id=None,
            call_id=f"call-{index}",
            status=category,
            duration_ms=index,
            arguments={"secret": "do-not-persist"},
            result={"secret": "do-not-persist", "result_truncated": category == "result_truncated"},
            policy_result={"allowed": category not in {"policy_denied", "cancelled"}},
            error_category=None if category == "succeeded" else category,
        )
    await db_session.commit()

    rows = await list_run_invocations(db_session, run_id=run.id)
    assert {row["status"] for row in rows} == set(categories)
    assert all("do-not-persist" not in str(row) for row in rows)
    persisted = (await db_session.execute(select(ExternalToolInvocation))).scalars().all()
    assert all(row.duration_ms >= 0 for row in persisted)
    assert all("secret" not in str(row.policy_result) for row in persisted)


@pytest.mark.asyncio
async def test_external_invocation_concurrency_limit_never_exceeds_one_slot():
    from app.executors.external_tool import (
        ExternalToolExecutionError,
        ExternalToolExecutor,
    )

    active = 0
    max_active = 0
    leases = 0

    async def acquire(_endpoint, *, wait_seconds, abort_check):
        nonlocal active, max_active, leases
        if active:
            return None
        active += 1
        max_active = max(max_active, active)
        leases += 1
        return f"lease-{leases}"

    async def release(_endpoint, _lease):
        nonlocal active
        active -= 1

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

        async def call_tool(self, _name, _arguments, *, timeout=None):
            await asyncio.sleep(0.02)
            return {"content": [{"type": "text", "text": "ok"}]}

    def executor():
        return ExternalToolExecutor(
            stable_name="mcp_concurrency_test",
            external_name="lookup",
            description="fixture",
            endpoint="https://fixture.example.test/rpc",
            input_schema={"type": "object"},
            permission_snapshot=[],
            required_permissions=[],
            client_factory=lambda *_args, **_kwargs: Client(),
            acquire_concurrency_slot=acquire,
            release_concurrency_slot=release,
        )

    async def call_one():
        try:
            await executor().execute({})
            return "succeeded"
        except ExternalToolExecutionError as exc:
            return exc.category

    results = await asyncio.gather(call_one(), call_one())
    assert max_active == 1
    assert sorted(results) == ["succeeded", "timeout"]
    assert active == 0


@pytest.mark.asyncio
async def test_external_invocation_uses_existing_user_quota_gate(
    client, db_session, make_headers, make_user, monkeypatch
):
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    run, _server, _version = await _run_fixture(client, db_session, headers, owner)
    run.cost_cents = 100
    run.status = "completed"
    await db_session.commit()
    monkeypatch.setattr(quota_service, "MONTHLY_QUOTA_CENTS", 100)

    with pytest.raises(quota_service.QuotaExceeded):
        await quota_service.assert_user_quota(db_session, owner.id)


# ---------- 真实执行链：调用记录的 step_id 必须指向已存在的步骤 ----------


class _FakeRedis:
    """最小 Redis 替身：仅覆盖并发租约用到的命令。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def eval(self, _script: str, _numkeys: int, key: str, token: str) -> int:
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0

    def close(self) -> None:
        return None


def _fixture_transport() -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        payload = await asyncio.to_thread(handle_message, message, scenario="normal")
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


class _ScriptedLLM:
    """第一次请求外部工具，第二次给出最终答案。"""

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        self.calls = 0

    async def chat(self, _messages, **_kwargs) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name=self.tool_name, arguments={"query": "fixture"})],
                cost_cents=1,
            )
        return LLMResponse(content="外部结果已消费", cost_cents=1)


@pytest.mark.asyncio
async def test_external_invocation_records_a_step_that_already_exists(
    client, db_session, make_headers, make_user, monkeypatch
):
    """部署验收发现的缺陷回归：调用记录先于步骤行落库 → 外键冲突 → Run 失败。

    执行器是在调用过程中写入 ``external_tool_invocations`` 的，而该行以
    ``step_id`` 引用步骤；因此 Worker 必须在外置工具执行**之前**先落步骤行
    （与沙箱路径同序），runner 的终态再回填同一行。
    """
    from app.services import external_concurrency, external_invocation_service

    suffix = uuid.uuid4().hex[:8]
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    agent = (
        await client.post(
            AGENTS_URL,
            headers=headers,
            json={
                "name": f"external-step-{suffix}",
                "description": "external step ordering",
                "system_prompt": "use the authorized tool",
                "model": "test-model",
            },
        )
    ).json()
    request = (
        await client.post(
            REQUESTS_URL,
            headers=headers,
            json={"title": "external step order", "description": "regression"},
        )
    ).json()

    stable_name = f"mcp_step_order_lookup_{suffix}"
    server = ExternalToolServer(
        owner_id=owner.id,
        created_by=owner.id,
        name=f"Step Order Server {suffix}",
        description="fixture",
        endpoint_url="https://8.8.8.8/step-order",
        normalized_endpoint_url="https://8.8.8.8/step-order",
        maintainer="Platform",
        auth_mode="none",
        status="published",
        current_validation_status="passed",
    )
    db_session.add(server)
    await db_session.flush()
    version = ExternalToolVersion(
        server_id=server.id,
        external_name="search",
        stable_name=stable_name,
        version="1.0.0+step-order",
        description="Search approved content",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        required_permissions=["records.read"],
        definition_hash=f"step-order-{suffix}",
        availability="valid",
    )
    db_session.add(version)
    await db_session.flush()
    listing = MarketplaceListing(
        tool_version_id=version.id,
        status="published",
        usage_summary="Search approved content",
        data_handling="Queries are sent to the approved server",
        restrictions={},
    )
    db_session.add(listing)
    await db_session.flush()
    installation = ToolInstallation(
        listing_id=listing.id,
        owner_id=owner.id,
        installed_by=owner.id,
        status="installed",
        version_policy="pinned",
    )
    db_session.add(installation)
    await db_session.flush()
    db_session.add(
        ExternalToolGrant(
            agent_id=agent["id"],
            installation_id=installation.id,
            tool_version_id=version.id,
            permission_snapshot=["records.read"],
            schema_snapshot={"input": version.input_schema, "output": None},
            status="active",
            granted_by=owner.id,
        )
    )
    await db_session.commit()

    assigned = await client.post(
        f"{REQUESTS_URL}/{request['id']}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert assigned.status_code == 202, assigned.text
    run_id = assigned.json()["run_id"]

    http = httpx.AsyncClient(transport=_fixture_transport())
    monkeypatch.setattr(external_concurrency, "_redis_client", _FakeRedis)
    monkeypatch.setattr("app.tasks.execute_run.LiteLLMClient.chat", _ScriptedLLM(stable_name).chat)

    # The executor binds its real MCP client as a constructor default, so inject
    # the fixture transport where the Worker builds the executors.
    real_executor = external_invocation_service.ExternalToolExecutor

    def _executor_with_fixture_transport(**kwargs):
        kwargs["client_factory"] = (
            lambda endpoint, **client_kwargs: MCPClient(
                endpoint, http_client=http, **client_kwargs
            )
        )
        return real_executor(**kwargs)

    monkeypatch.setattr(
        external_invocation_service, "ExternalToolExecutor", _executor_with_fixture_transport
    )

    run = await db_session.get(Run, run_id)
    assert run is not None
    agent_row = await db_session.get(Agent, agent["id"])
    assert agent_row is not None
    try:
        await _execute(db_session, run, agent_row)
    finally:
        await http.aclose()

    db_session.expire_all()
    await db_session.refresh(run)
    assert run.status == "completed", run.error

    invocation = await db_session.scalar(
        select(ExternalToolInvocation).where(ExternalToolInvocation.run_id == run_id)
    )
    assert invocation is not None
    assert invocation.status == "succeeded"
    assert invocation.step_id is not None

    step = await db_session.get(Step, invocation.step_id)
    assert step is not None, "调用记录引用了一个不存在的步骤行"
    assert step.run_id == run_id
    assert step.kind == "tool"
    assert step.tool_name == stable_name
    # 终态由 runner 回填：预置的 running 占位不能残留。
    assert step.status == "completed"
    assert step.input == {"argument_keys": ["query"]}
    assert step.output["status"] == "succeeded"
    assert step.output["error_category"] is None

    steps = (
        (await db_session.execute(select(Step.id).where(Step.run_id == run_id)))
        .scalars()
        .all()
    )
    assert invocation.step_id in steps
    assert [s for s in steps if s == invocation.step_id].count(invocation.step_id) == 1
