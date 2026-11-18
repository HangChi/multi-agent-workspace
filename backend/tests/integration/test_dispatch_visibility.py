"""007 可见性与 assign-user 集成测试（T017，US1/US3/US4/US6）。

锁的是 3b 的契约变更面：
- 访问权谓词 owner-or-assignee（A-5 陌生人 404，assignee 可操作）；
- GET /requests 默认 scope=relevant（R-1..R-5），mine 向后兼容；
- assign 扩展 assignee_type=user（A-1/A-2/A-3/A-4/A-7）；
- assignee 把 accepted 需求拖给 agent（A-8），配额记 owner（SC-005）；
- pending 未接收拖 agent → 409（A-9）。

setup 全部 commit（API 走独立 session，看不到未提交数据）。
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.request import Request
from app.models.run import Run
from app.models.user import User
from app.services import quota_service

pytestmark = pytest.mark.integration


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"vis-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _agent(db, owner: User) -> Agent:
    agent = Agent(
        owner_id=owner.id,
        name=f"agent-{uuid.uuid4().hex[:6]}",
        system_prompt="p",
        model="gpt-4o-mini",
        temperature=0.5,
        max_steps=4,
        tools=[],
        status="active",
    )
    db.add(agent)
    await db.commit()
    return agent


async def _request(db, owner: User, **kw) -> Request:
    kw.setdefault("title", f"REQ-{uuid.uuid4().hex[:6]}")
    req = Request(owner_id=owner.id, **kw)
    db.add(req)
    await db.commit()
    return req


async def _dispatch_via_api(client, headers, req, target, note=None):
    body = {"assignee_type": "user", "assignee_id": target.id}
    if note:
        body["note"] = note
    return await client.post(
        f"/api/v1/requests/{req.id}/assign", json=body, headers=headers
    )


# ---------- 可见性：访问权谓词 ----------


async def test_stranger_cannot_see_or_touch_request(db_session, client, make_headers):
    """A-5：非创建者非接收人 → 详情 404、assign 404（不暴露存在性）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    stranger = await _user(db_session)
    req = await _request(db_session, owner)

    resp = await _dispatch_via_api(
        client, await make_headers(owner), req, worker, note="请跟进"
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"

    s_headers = await make_headers(stranger)
    # 既有端点维持 006 契约：非相关人员 403 FORBIDDEN（test_requests.py 锁定，
    # FR-014 零变化）；007 新分派端点用 404（dispatch_service 口令径）。
    detail = await client.get(f"/api/v1/requests/{req.id}", headers=s_headers)
    assert detail.status_code == 403
    assign = await client.post(
        f"/api/v1/requests/{req.id}/assign",
        json={"assignee_type": "user", "assignee_id": stranger.id},
        headers=s_headers,
    )
    assert assign.status_code == 404


async def test_assignee_can_see_dispatched_request(db_session, client, make_headers):
    """A-1 + US4：接收人可见分派来的需求详情（assignee 字段 + 经手链）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch_via_api(
        client, await make_headers(owner), req, worker, note="请跟进"
    )

    detail = await client.get(
        f"/api/v1/requests/{req.id}", headers=await make_headers(worker)
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["assignee_id"] == worker.id
    assert body["assignee_email"] == worker.email
    assert body["status"] == "pending"
    history = body["dispatch_history"]
    assert len(history) == 1
    assert history[0]["action"] == "dispatch"
    assert history[0]["to_assignee_id"] == worker.id


# ---------- scope：relevant 默认 / mine 兼容 ----------


async def test_default_scope_is_relevant(db_session, client, make_headers):
    """R-1：不传 scope → 我创建的 + 分派给我的（并集）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    mine = await _request(db_session, owner, title="我的需求")
    dispatched = await _request(db_session, owner, title="派给工人的")
    await _dispatch_via_api(client, await make_headers(owner), dispatched, worker)

    resp = await client.get("/api/v1/requests", headers=await make_headers(worker))
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert dispatched.id in ids
    assert mine.id not in ids  # 别人的需求不进来


async def test_scope_mine_is_backward_compatible(db_session, client, make_headers):
    """R-2：scope=mine 与 006 行为一致——只看自己创建的。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    mine = await _request(db_session, owner, title="我的需求")
    dispatched = await _request(db_session, owner, title="派给工人的")
    await _dispatch_via_api(client, await make_headers(owner), dispatched, worker)

    resp = await client.get(
        "/api/v1/requests?scope=mine", headers=await make_headers(owner)
    )
    ids = {item["id"] for item in resp.json()["items"]}
    assert mine.id in ids
    assert dispatched.id in ids  # 我创建的仍在（mine 不排除已分派的）


async def test_status_filter_accepts_pending(db_session, client, make_headers):
    """R-4：status=pending 过滤可用（枚举扩展后 API 层放行）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch_via_api(client, await make_headers(owner), req, worker)

    resp = await client.get(
        "/api/v1/requests?status=pending&scope=relevant",
        headers=await make_headers(worker),
    )
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["items"]}
    assert req.id in ids


# ---------- assign user：分派/转交/边界 ----------


async def test_reassign_via_assign_endpoint(db_session, client, make_headers):
    """A-2：pending 再拖给另一人 → 转交（历史 dispatch+reassign，旧接收人收通知）。"""
    owner = await _user(db_session)
    w1 = await _user(db_session)
    w2 = await _user(db_session)
    req = await _request(db_session, owner)
    await _dispatch_via_api(client, await make_headers(owner), req, w1)

    resp = await _dispatch_via_api(client, await make_headers(owner), req, w2)
    assert resp.status_code == 202

    detail = await client.get(
        f"/api/v1/requests/{req.id}", headers=await make_headers(owner)
    )
    actions = [h["action"] for h in detail.json()["dispatch_history"]]
    assert actions == ["dispatch", "reassign"]


async def test_dispatch_to_disabled_user_404(db_session, client, make_headers):
    """A-3：禁用账号不可作为接收人。"""
    owner = await _user(db_session)
    gone = await _user(db_session)
    gone.status = "disabled"
    db_session.add(gone)
    await db_session.commit()
    req = await _request(db_session, owner)

    resp = await _dispatch_via_api(client, await make_headers(owner), req, gone)
    assert resp.status_code == 404


async def test_self_dispatch_allowed(db_session, client, make_headers):
    """A-4：自派允许（单人闭环）。"""
    owner = await _user(db_session)
    req = await _request(db_session, owner)

    resp = await _dispatch_via_api(client, await make_headers(owner), req, owner)
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"


async def test_dispatch_owner_quota_gate_429(
    db_session, client, make_headers, monkeypatch
):
    """A-7 / FR-009：owner 月配额超限 → 分派也 429（配额跟需求方走）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    # owner 名下一条已计成本的 completed Run，把月用量顶到上限
    spent = await _request(db_session, owner, title="已花钱的需求")
    db_session.add(
        Run(
            request_id=spent.id,
            owner_id=owner.id,
            assignee_type="agent",
            assignee_id=str(uuid.uuid4()),
            strategy="direct",
            status="completed",
            cost_cents=100,
        )
    )
    await db_session.commit()
    monkeypatch.setattr(quota_service, "MONTHLY_QUOTA_CENTS", 100)

    resp = await _dispatch_via_api(
        client, await make_headers(owner), await _request(db_session, owner), worker
    )
    assert resp.status_code == 429


# ---------- assignee 执行 + 配额口径 ----------


async def test_assignee_runs_dispatched_request_quota_on_owner(
    db_session, client, make_headers
):
    """A-8 / SC-005：接收人把 accepted 需求拖给自己的 agent → Run 创建；
    成本口径恒记 owner（Run.owner_id 继承 + 配额入参为 owner）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    agent = await _agent(db_session, worker)
    req = await _request(db_session, owner)
    await _dispatch_via_api(client, await make_headers(owner), req, worker)

    accepted = await client.post(
        f"/api/v1/requests/{req.id}/accept", headers=await make_headers(worker)
    )
    assert accepted.status_code == 202

    resp = await client.post(
        f"/api/v1/requests/{req.id}/assign",
        json={"assignee_type": "agent", "assignee_id": agent.id},
        headers=await make_headers(worker),
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    run = (
        (await db_session.execute(select(Run).where(Run.id == run_id))).scalars().one()
    )
    assert run.owner_id == owner.id  # 成本恒记需求创建者


async def test_assign_to_agent_before_accept_409(db_session, client, make_headers):
    """A-9：未接收（pending）直接拖 agent → 409，状态不变。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    agent = await _agent(db_session, worker)
    req = await _request(db_session, owner)
    await _dispatch_via_api(client, await make_headers(owner), req, worker)

    resp = await client.post(
        f"/api/v1/requests/{req.id}/assign",
        json={"assignee_type": "agent", "assignee_id": agent.id},
        headers=await make_headers(worker),
    )
    assert resp.status_code == 409
    current = (
        await db_session.execute(select(Request.status).where(Request.id == req.id))
    ).scalar()
    assert current == "pending"


async def test_concurrent_dispatch_single_winner(db_session, client, make_headers):
    """A-10：两人同时分派同一需求 → 行级锁串行化，无丢失更新。

    与 accept/reject 不同（那两个是 202+409）：dispatch 在 pending 上再拖人
    的语义就是**转交**（US1 第二场景），所以并发双 dispatch 的正确结果是
    一条 dispatch + 一条 reassign、都 202，最终接收人是后者——历史完整，
    不存在「派给了 A 但系统以为是 B」的静默丢失。契约 §3 A-10 已按此修订。
    """
    owner = await _user(db_session)
    w1 = await _user(db_session)
    w2 = await _user(db_session)
    req = await _request(db_session, owner)
    headers = await make_headers(owner)

    async def _try(target):
        return await _dispatch_via_api(client, headers, req, target)

    r1, r2 = await asyncio.gather(_try(w1), _try(w2))
    assert r1.status_code == r2.status_code == 202

    detail = await client.get(f"/api/v1/requests/{req.id}", headers=headers)
    body = detail.json()
    actions = [h["action"] for h in body["dispatch_history"]]
    assert actions == ["dispatch", "reassign"]
    assert body["assignee_id"] in (w1.id, w2.id)
    # 最终接收人与最后一条历史一致（无丢失更新）
    assert body["assignee_id"] == body["dispatch_history"][-1]["to_assignee_id"]


# ---------- Run 访问权扩展（US3「实时可见」对经手人生效） ----------


async def test_assignee_can_view_run_of_dispatched_request(
    db_session, client, make_headers
):
    """US3：接收人执行分派需求后，Run 详情对接收人可见（进度/回复/取消）。"""
    owner = await _user(db_session)
    worker = await _user(db_session)
    stranger = await _user(db_session)
    agent = await _agent(db_session, worker)
    req = await _request(db_session, owner)
    await _dispatch_via_api(client, await make_headers(owner), req, worker)
    await client.post(
        f"/api/v1/requests/{req.id}/accept", headers=await make_headers(worker)
    )
    resp = await client.post(
        f"/api/v1/requests/{req.id}/assign",
        json={"assignee_type": "agent", "assignee_id": agent.id},
        headers=await make_headers(worker),
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    # 接收人（执行人）可查 Run 详情
    detail = await client.get(
        f"/api/v1/runs/{run_id}", headers=await make_headers(worker)
    )
    assert detail.status_code == 200
    # 创建者（Run.owner）当然也可查
    owner_view = await client.get(
        f"/api/v1/runs/{run_id}", headers=await make_headers(owner)
    )
    assert owner_view.status_code == 200
    # 陌生人仍 404（不暴露存在性）
    stranger_view = await client.get(
        f"/api/v1/runs/{run_id}", headers=await make_headers(stranger)
    )
    assert stranger_view.status_code == 404
