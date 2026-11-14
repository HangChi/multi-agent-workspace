"""审计查询端点集成测试（T079）：GET /api/v1/audit-logs 筛选与分页（对齐 tasks.md T079）。

覆盖：
- 未认证 401
- member/viewer 403（admin 专属）
- 全部返回（不筛选）：items + total
- user_id 筛选：只返回该用户的日志
- action 筛选：只返回该动作的日志
- user_id + action 组合筛选
- 分页：page_size + page 生效，total 不变
- 排序：created_at 降序（最新在前）

⚠️ 数据隔离策略：审计行由 commit 落库后无法被 session rollback 回滚，
因此跨测试会累积。所有断言依赖 user_id 隔离（每次新用户）或
action 唯一化（uuid 后缀），避免被前序测试残留污染。
"""

import uuid

from app.core.audit import write_audit


async def _seed_audit(db_session, *, user_id: str, action: str, n: int = 1) -> None:
    """直接调 write_audit 造审计日志（跨 n 条同 action），commit 保证 client 可见。"""
    for _ in range(n):
        await write_audit(db_session, user_id=user_id, action=action)
    await db_session.commit()


async def test_unauthenticated(client):
    """未认证访问审计端点 → 401。"""
    resp = await client.get("/api/v1/audit-logs")
    assert resp.status_code == 401


async def test_member_forbidden(client, make_headers):
    """member 访问 → 403。"""
    headers = await make_headers(role="member")
    resp = await client.get("/api/v1/audit-logs", headers=headers)
    assert resp.status_code == 403


async def test_viewer_forbidden(client, make_headers):
    """viewer 访问 → 403。"""
    headers = await make_headers(role="viewer")
    resp = await client.get("/api/v1/audit-logs", headers=headers)
    assert resp.status_code == 403


async def test_admin_list_all(client, db_session, make_headers, make_user):
    """admin 拉取全部（不筛选）：items + total，最新在前。

    审计行 commit 后无法回滚（跨测试残留），且全量回归时更早的文件
    （test_agents 等）已写入审计 → total 用 >= 断言；最新两条为本测试
    seed 数据，排序断言成立。
    """
    admin, _ = await make_user(role="admin")
    headers = await make_headers(user=admin)

    u1, _ = await make_user()
    await _seed_audit(db_session, user_id=u1.id, action="agent.create")
    await _seed_audit(db_session, user_id=u1.id, action="agent.delete")

    resp = await client.get("/api/v1/audit-logs", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2

    # 输出字段完整（任意一条即可，跨文件残留不影响字段形状）
    first = body["items"][0]
    assert set(first.keys()) == {
        "id", "user_id", "action", "target_type", "target_id", "detail", "created_at",
    }

    # 排序与相对顺序：筛选本用户，两条 seed 日志最新在前
    mine = await client.get(
        "/api/v1/audit-logs", headers=headers, params={"user_id": u1.id}
    )
    mbody = mine.json()
    assert mbody["total"] == 2
    assert mbody["items"][0]["action"] == "agent.delete"
    assert mbody["items"][1]["action"] == "agent.create"


async def test_filter_by_user(client, db_session, make_headers, make_user):
    """?user_id= 只返回该用户日志。"""
    admin, _ = await make_user(role="admin")
    headers = await make_headers(user=admin)

    u1, _ = await make_user()
    u2, _ = await make_user()
    await _seed_audit(db_session, user_id=u1.id, action="agent.create")
    await _seed_audit(db_session, user_id=u2.id, action="agent.create")

    resp = await client.get(f"/api/v1/audit-logs?user_id={u1.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert all(item["user_id"] == u1.id for item in body["items"])


async def test_filter_by_action(client, db_session, make_headers, make_user):
    """?action= 只返回该动作日志（action 唯一化避免污染）。"""
    admin, _ = await make_user(role="admin")
    headers = await make_headers(user=admin)

    u1, _ = await make_user()
    action = f"agent.create-{uuid.uuid4().hex[:8]}"
    await _seed_audit(db_session, user_id=u1.id, action=action, n=2)
    await _seed_audit(db_session, user_id=u1.id, action="agent.update")

    resp = await client.get(f"/api/v1/audit-logs?action={action}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(item["action"] == action for item in body["items"])


async def test_filter_combined(client, db_session, make_headers, make_user):
    """user_id + action 组合筛选。"""
    admin, _ = await make_user(role="admin")
    headers = await make_headers(user=admin)

    u1, _ = await make_user()
    u2, _ = await make_user()
    await _seed_audit(db_session, user_id=u1.id, action="agent.create")
    await _seed_audit(db_session, user_id=u1.id, action="agent.delete")
    await _seed_audit(db_session, user_id=u2.id, action="agent.create")

    resp = await client.get(
        f"/api/v1/audit-logs?user_id={u1.id}&action=agent.create", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "agent.create"
    assert body["items"][0]["user_id"] == u1.id


async def test_pagination(client, db_session, make_headers, make_user):
    """分页：page_size + page，total 不变（action 唯一化避免污染）。"""
    admin, _ = await make_user(role="admin")
    headers = await make_headers(user=admin)

    u1, _ = await make_user()
    action = f"login-{uuid.uuid4().hex[:8]}"
    await _seed_audit(db_session, user_id=u1.id, action=action, n=5)

    # 第一页 2 条
    resp = await client.get(f"/api/v1/audit-logs?page=1&page_size=2&action={action}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2

    # 第三页 1 条（共 5 条，page_size=2 → 3 页）
    resp = await client.get(f"/api/v1/audit-logs?page=3&page_size=2&action={action}", headers=headers)
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 1

    # 超页返回空列表，total 不变
    resp = await client.get(f"/api/v1/audit-logs?page=99&page_size=2&action={action}", headers=headers)
    body = resp.json()
    assert body["total"] == 5
    assert body["items"] == []


async def test_audit_created_by_user_action(client, db_session, make_headers, make_user):
    """真实链路：登录写审计日志（auth 已接入 write_audit，action=user_login）。"""
    admin, _ = await make_user(role="admin")
    headers = await make_headers(user=admin)

    u1, password = await make_user()
    # 通过登录接口触发审计写入
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": u1.email, "password": password},
    )
    assert resp.status_code == 200

    resp = await client.get(
        f"/api/v1/audit-logs?user_id={u1.id}&action=user_login", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["action"] == "user_login" for item in body["items"])
    assert all(item["user_id"] == u1.id for item in body["items"])
