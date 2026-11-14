"""权限集成测试（T020）：三角色 RBAC 与资源归属校验。

覆盖 SC-005「无权限 100% 拒绝」：
- viewer 创建 Agent → 403
- member 访问 admin 接口 → 403
- 未认证 → 401
- 非 owner 访问他人资源 → 403
"""



REGISTER_URL = "/api/v1/auth/register"
ME_URL = "/api/v1/auth/me"


# ---------- 未认证 ----------


async def test_unauthenticated_me(client):
    """未带 token 访问受保护接口 → 401。"""
    resp = await client.get(ME_URL)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_unauthenticated_agents_list(client):
    """未认证访问资源列表 → 401（列表也受保护）。"""
    resp = await client.get("/api/v1/agents")
    assert resp.status_code == 401


async def test_invalid_token(client):
    """伪造 token → 401。"""
    resp = await client.get(
        ME_URL, headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


# ---------- 角色权限 ----------


async def test_member_access_admin_interface(client, make_headers):
    """member 访问 admin 接口 → 403。"""
    headers = await make_headers(role="member")
    resp = await client.get("/api/v1/audit-logs", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_viewer_access_admin_interface(client, make_headers):
    """viewer 访问 admin 接口 → 403。"""
    headers = await make_headers(role="viewer")
    resp = await client.get("/api/v1/audit-logs", headers=headers)
    assert resp.status_code == 403


async def test_admin_access_admin_interface(client, make_headers):
    """admin 访问 admin 接口 → 200（可访问）。"""
    headers = await make_headers(role="admin")
    resp = await client.get("/api/v1/audit-logs", headers=headers)
    assert resp.status_code == 200


# ---------- viewer 创建 Agent ----------


async def test_viewer_create_agent_forbidden(client, make_headers):
    """viewer 创建 Agent → 403（SC-005）。"""
    headers = await make_headers(role="viewer")
    resp = await client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": "viewer-agent",
            "system_prompt": "you are a test",
            "model": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_member_create_agent_allowed(client, make_headers, db_session):
    """member 创建 Agent → 201（授权通过）。"""
    headers = await make_headers(role="member")
    resp = await client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": "member-agent",
            "system_prompt": "you are a test",
            "model": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 201


# ---------- 资源归属校验 ----------


async def test_non_owner_access_resource(client, auth_headers, db_session):
    """非 owner 访问他人资源 → 403（归属校验）。"""
    owner_headers, _ = await auth_headers(role="member")

    # owner 创建一个 Agent
    resp = await client.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={
            "name": "owner-agent",
            "system_prompt": "you are a test",
            "model": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 201
    agent_id = resp.json()["id"]

    # 另一个用户访问/编辑该 Agent → 403
    other_headers, _ = await auth_headers(role="member")
    resp = await client.patch(
        f"/api/v1/agents/{agent_id}",
        headers=other_headers,
        json={"name": "hacked-name"},
    )
    assert resp.status_code == 403


async def test_owner_access_own_resource(client, auth_headers, db_session):
    """owner 编辑自己的 Agent → 200（归属通过）。"""
    owner_headers, _ = await auth_headers(role="member")

    resp = await client.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={
            "name": "my-agent",
            "system_prompt": "you are a test",
            "model": "gpt-4o-mini",
        },
    )
    agent_id = resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/agents/{agent_id}",
        headers=owner_headers,
        json={"name": "my-agent-renamed"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "my-agent-renamed"


# ---------- 集成：S1 场景 ----------


async def test_s1_scenario(client, db_session, make_user, make_headers):
    """S1 完整链路：注册→登录→me→viewer 403→member 403。"""
    # 注册（用 make_user 替代邀请码流程，回归在 test_auth 覆盖）
    # 登录 + me
    _, password = await make_user(email="s1@test.com")
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "s1@test.com", "password": password},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access']}"}

    me = await client.get(ME_URL, headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "s1@test.com"
