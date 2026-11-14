"""运行 API 集成测试（T063，US5）：runs 端点契约（FR-016~019/FR-021）。

对齐 contracts/api.md：
- GET /runs/{id} → 200（含 steps 列表摘要、cost、result、error、artifacts）
- POST /runs/{id}/cancel → 200 {"status": "cancelled"}；终态 → 409（FR-018）
- POST /runs/{id}/retry → 202 {"status": "queued"}（仅 failed/timeout，FR-017）
- POST /runs/{id}/reply → 200 {"status": "running"}（仅 needs_input，FR-021）
- GET /runs/{id}/artifacts/{artifact_id} → 302 预签名（FR-016）

在 TESTING=1 下走真实 FastAPI + 测试库；取消/重试/回复直接改库模拟状态，
不依赖 Celery Worker（Worker 链路由 test_execution_flow.py T076 覆盖）。
"""

import uuid

from sqlalchemy import select

from app.models.artifact import Artifact
from app.models.request import Request
from app.models.run import Run
from app.models.step import Step

REQUESTS_URL = "/api/v1/requests"
AGENTS_URL = "/api/v1/agents"
RUNS_URL = "/api/v1/runs"


async def _create_agent(client, headers: dict) -> dict:
    resp = await client.post(
        AGENTS_URL,
        headers=headers,
        json={
            "name": f"agent-{uuid.uuid4().hex[:6]}",
            "system_prompt": "you are helpful",
            "model": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _create_request(client, headers: dict) -> dict:
    resp = await client.post(
        REQUESTS_URL,
        headers=headers,
        json={
            "title": f"run-{uuid.uuid4().hex[:6]}",
            "description": "desc",
            "priority": "normal",
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _setup_run(client, headers: dict, db_session, status: str) -> Run:
    """创建「需求 + 分配 → queued Run」，再直接改库模拟到目标状态。"""
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)
    assign_resp = await client.post(
        f"{REQUESTS_URL}/{req['id']}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert assign_resp.status_code == 202
    run_id = assign_resp.json()["run_id"]

    run = await db_session.get(Run, run_id)
    assert run is not None
    run.status = status
    await db_session.commit()
    await db_session.refresh(run)
    return run


# ---------- GET /runs/{id} 详情 ----------


async def test_run_detail_includes_steps_cost_result_error_artifacts(
    client, make_headers, db_session
):
    """详情 200：含 steps/cost/result/error/artifacts 字段。"""
    headers = await make_headers()
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)
    resp = await client.post(
        f"{REQUESTS_URL}/{req['id']}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    # 模拟执行：写 steps + result + cost + artifact
    run = await db_session.get(Run, run_id)
    run.status = "running"
    run.cost_cents = 123
    await db_session.commit()
    await db_session.refresh(run)

    step = Step(run_id=run.id, seq=1, kind="llm", status="completed", cost_cents=120)
    db_session.add(step)
    art = Artifact(
        run_id=run.id,
        object_key=f"runs/{run.id}/report.md",
        filename="report.md",
        size_bytes=2048,
        mime_type="text/markdown",
    )
    db_session.add(art)
    await db_session.commit()
    await db_session.refresh(run)

    resp = await client.get(f"{RUNS_URL}/{run_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == run_id
    assert body["status"] == "running"
    assert body["cost_cents"] == 123
    assert body["result"] is None or "result" in body
    assert "error" in body
    assert len(body["steps"]) == 1
    assert body["steps"][0]["kind"] == "llm"
    assert body["steps"][0]["cost_cents"] == 120
    assert len(body["artifacts"]) == 1
    assert body["artifacts"][0]["filename"] == "report.md"
    assert body["artifacts"][0]["size_bytes"] == 2048


async def test_run_detail_not_found(client, make_headers):
    """不存在的 run → 404。"""
    headers = await make_headers()
    resp = await client.get(f"{RUNS_URL}/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


async def test_run_detail_other_users_run_404(client, make_headers):
    """他人 Run → 404（不可见）。"""
    headers_a = await make_headers()
    agent = await _create_agent(client, headers_a)
    req = await _create_request(client, headers_a)
    resp = await client.post(
        f"{REQUESTS_URL}/{req['id']}/assign",
        headers=headers_a,
        json={"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    run_id = resp.json()["run_id"]

    headers_b = await make_headers()
    resp = await client.get(f"{RUNS_URL}/{run_id}", headers=headers_b)
    assert resp.status_code == 404


# ---------- POST /runs/{id}/cancel ----------


async def test_cancel_queued_run(client, make_headers, db_session):
    """排队中取消 → 200 cancelled（FR-018）。"""
    headers = await make_headers()
    run = await _setup_run(client, headers, db_session, status="queued")
    resp = await client.post(f"{RUNS_URL}/{run.id}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    await db_session.refresh(run)
    assert run.status == "cancelled"
    assert run.finished_at is not None


async def test_cancel_running_run(client, make_headers, db_session):
    """执行中取消 → 200 cancelled。"""
    headers = await make_headers()
    run = await _setup_run(client, headers, db_session, status="running")
    resp = await client.post(f"{RUNS_URL}/{run.id}/cancel", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


async def test_cancel_terminal_run_conflict(client, make_headers, db_session):
    """终态取消 → 409（FR-018：终态不可取消）。"""
    headers = await make_headers()
    run = await _setup_run(client, headers, db_session, status="completed")
    resp = await client.post(f"{RUNS_URL}/{run.id}/cancel", headers=headers)
    assert resp.status_code == 409


# ---------- POST /runs/{id}/retry ----------


async def test_retry_failed_run(client, make_headers, db_session):
    """failed 重试 → 202 queued（FR-017）。"""
    headers = await make_headers()
    run = await _setup_run(client, headers, db_session, status="failed")
    run.error = "LLM 调用失败"
    run.cost_cents = 50
    run.current_step = 4
    await db_session.commit()

    resp = await client.post(f"{RUNS_URL}/{run.id}/retry", headers=headers)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    await db_session.refresh(run)
    assert run.status == "queued"
    assert run.current_step == 0
    assert run.cost_cents == 0
    assert run.error is None


async def test_retry_timeout_run(client, make_headers, db_session):
    """timeout 重试 → 202 queued。"""
    headers = await make_headers()
    run = await _setup_run(client, headers, db_session, status="timeout")
    resp = await client.post(f"{RUNS_URL}/{run.id}/retry", headers=headers)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"


async def test_retry_completed_run_conflict(client, make_headers, db_session):
    """completed 不可重试 → 409。"""
    headers = await make_headers()
    run = await _setup_run(client, headers, db_session, status="completed")
    resp = await client.post(f"{RUNS_URL}/{run.id}/retry", headers=headers)
    assert resp.status_code == 409


# ---------- POST /runs/{id}/reply ----------


async def test_reply_needs_input_resumes(client, make_headers, db_session):
    """needs_input 回复 → 200 running（FR-021）。"""
    headers = await make_headers()
    run = await _setup_run(client, headers, db_session, status="needs_input")
    resp = await client.post(
        f"{RUNS_URL}/{run.id}/reply", headers=headers, json={"message": "客户名单：ABC 公司"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    await db_session.refresh(run)
    assert run.status == "running"


async def test_reply_non_needs_input_conflict(client, make_headers, db_session):
    """非 needs_input 状态回复 → 409。"""
    headers = await make_headers()
    run = await _setup_run(client, headers, db_session, status="running")
    resp = await client.post(
        f"{RUNS_URL}/{run.id}/reply", headers=headers, json={"message": "x"}
    )
    assert resp.status_code == 409


# ---------- GET /runs/{id}/artifacts/{artifact_id} ----------


async def test_artifact_download_redirects(client, make_headers, db_session):
    """制品下载 → 302 到预签名 URL（FR-016）。"""
    headers = await make_headers()
    agent = await _create_agent(client, headers)
    req = await _create_request(client, headers)
    resp = await client.post(
        f"{REQUESTS_URL}/{req['id']}/assign",
        headers=headers,
        json={"assignee_type": "agent", "assignee_id": agent["id"]},
    )
    run_id = resp.json()["run_id"]

    run = await db_session.get(Run, run_id)
    art = Artifact(
        run_id=run.id,
        object_key=f"runs/{run.id}/report.pdf",
        filename="report.pdf",
        size_bytes=1024,
        mime_type="application/pdf",
    )
    db_session.add(art)
    await db_session.commit()

    resp = await client.get(
        f"{RUNS_URL}/{run_id}/artifacts/{art.id}", headers=headers, follow_redirects=False
    )
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert location.startswith("http") or location.startswith("/")
