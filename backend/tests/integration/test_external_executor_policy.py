"""`build_external_executors` 的 fail-closed 判定（R4 前置：拆之前先有覆盖）。

为什么单独写这个文件：这个函数有 244 行、五组策略判定、凭据解密和两个闭包，
**但此前零测试**——`grep build_external_executors tests/` 一条都没有。所有外部
工具用例都手工 `ExternalToolExecutor(...)` 构造，把这段全绕过去了。244 行零
覆盖的代码不动手拆，否则拆完靠什么证明行为没变。

覆盖重点（都是「不满足条件就必须 fail closed」的方向）：

1. 五组状态快照不一致 → `policy_error`；
2. 快照权限 ⊄ 当前授权 → `permissions_changed`（**取交集**交给 executor，
   不是取快照）；
3. 凭据缺失 / 已吊销 / 跨 server / 跨 owner / 解密失败 → `credential_unavailable`；
4. endpoint 不可用 → `endpoint_unavailable`（stdio 不做 URL 校验，它没有 URL）；
5. `authorize_current` 是 call-time 复核：执行器在 Run 生命周期内会被反复调用，
   状态可能在两次调用之间变了，**必须每次重查**而不是信构造时的快照。

优先级也钉住了：`policy_error` 是**后写覆盖先写**，顺序为
状态 → 权限 → 凭据 → endpoint。改顺序会让「同一条记录同时满足两个失败条件时
报哪个」发生变化，而这件事没人会去读 244 行确认。
"""

import pytest

from app.core.config import settings
from app.core.secrets import CredentialCipher
from app.models.external_credential import ExternalCredential
from app.models.external_tool import (
    ExternalToolGrant,
    ExternalToolServer,
    ExternalToolVersion,
    MarketplaceListing,
    RunExternalToolSnapshot,
    ToolInstallation,
)
from app.models.run import Run
from app.services.external_invocation_service import build_external_executors

# 必须用**应用真实配置**的密钥加密：`build_external_executors` 用
# `settings.mcp_credential_key` 解密，两边不一致就会落到"解密失败 →
# credential_unavailable"，看着像策略判定，其实是测试自己把 key 写错了。
CIPHER_KEY = settings.mcp_credential_key


async def _build_chain(db_session, owner, *, agent, **overrides):
    """搭起 7 表链条，返回可继续改动的各对象。

    故意全部走真实 ORM 对象 + 真实外键：这段代码的价值就在于「按 join 读当前
    状态」，用假的 row 元组就测不到 join 条件写错（比如漏了
    `ToolInstallation.owner_id == owner_id`，会让别人的安装被自己用）。
    """
    server_kwargs = {
        "owner_id": owner.id,
        "created_by": owner.id,
        "name": "Chain Server",
        "description": "fixture",
        "endpoint_url": "https://8.8.8.8/mcp",
        "normalized_endpoint_url": "https://8.8.8.8/mcp",
        "maintainer": "Platform",
        "auth_mode": "none",
        "status": "published",
        "current_validation_status": "passed",
    }
    server_kwargs.update(overrides.pop("server", {}))
    server = ExternalToolServer(**server_kwargs)
    db_session.add(server)
    await db_session.flush()

    version = ExternalToolVersion(
        server_id=server.id,
        external_name="lookup",
        stable_name="mcp_chain_lookup_v1",
        version="1.0.0+chain",
        description="Lookup",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        required_permissions=["records.read"],
        definition_hash="chain-digest",
        availability="valid",
    )
    db_session.add(version)
    await db_session.flush()

    listing = MarketplaceListing(
        tool_version_id=version.id,
        status="published",
        usage_summary="read records",
        data_handling="no storage",
        restrictions={},
    )
    db_session.add(listing)
    await db_session.flush()

    installation = ToolInstallation(
        listing_id=listing.id,
        owner_id=owner.id,
        status="installed",
        version_policy="pinned",
        installed_by=owner.id,
    )
    db_session.add(installation)
    await db_session.flush()

    grant = ExternalToolGrant(
        agent_id=agent.id,
        installation_id=installation.id,
        tool_version_id=version.id,
        permission_snapshot=["records.read"],
        schema_snapshot={"input": {"type": "object"}},
        status="active",
        granted_by=owner.id,
    )
    db_session.add(grant)
    await db_session.flush()

    run = Run(
        request_id=overrides.pop("request_id"),
        owner_id=owner.id,
        assignee_type="agent",
        assignee_id=agent.id,
        strategy="direct",
        status="running",
    )
    db_session.add(run)
    await db_session.flush()

    snapshot = RunExternalToolSnapshot(
        run_id=run.id,
        grant_id=grant.id,
        server_id=server.id,
        tool_version_id=version.id,
        stable_name="mcp_chain_lookup_v1",
        external_name="lookup",
        version="1.0.0+chain",
        permission_snapshot=["records.read"],
        schema_snapshot={"input": {"type": "object"}},
        credential_id=None,
    )
    db_session.add(snapshot)
    await db_session.flush()
    await db_session.commit()

    objects = {
        "server": server,
        "version": version,
        "listing": listing,
        "installation": installation,
        "grant": grant,
        "run": run,
        "snapshot": snapshot,
    }
    objects.update(overrides)
    return objects


@pytest.fixture
async def chain(client, db_session, make_user, make_headers):
    """默认构造一条全部合法的链条（健康路径），测试按需破坏其中一处。"""
    owner, _ = await make_user(role="member")
    headers = await make_headers(user=owner)
    request = await client.post(
        "/api/v1/requests",
        headers=headers,
        json={"title": "chain", "description": "chain", "priority": "normal"},
    )
    assert request.status_code == 201, request.text

    from app.models.agent import Agent

    agent = Agent(
        owner_id=owner.id,
        name="Chain Agent",
        system_prompt="p",
        model="gpt-4o-mini",
    )
    db_session.add(agent)
    await db_session.flush()

    objs = await _build_chain(
        db_session, owner, agent=agent, request_id=request.json()["id"]
    )
    objs["owner"] = owner
    objs["agent"] = agent
    return objs


async def _executor_for(db_session, objs):
    return (
        await build_external_executors(
            db_session, run_id=objs["run"].id, owner_id=objs["owner"].id
        )
    ).get("mcp_chain_lookup_v1")


async def test_healthy_chain_produces_a_usable_executor(db_session, chain) -> None:
    """先证明健康路径成立——否则下面每个「报错」的测试都可能是别的原因报的。"""
    executor = await _executor_for(db_session, chain)

    assert executor is not None
    assert executor.policy_error is None
    assert executor.endpoint == "https://8.8.8.8/mcp"
    assert executor.input_schema == {"type": "object"}
    assert executor.permission_snapshot == frozenset({"records.read"})


@pytest.mark.parametrize(
    "field, value",
    [
        ("server", {"status": "paused"}),
        ("version", {"availability": "invalid"}),
        ("grant", {"status": "revoked"}),
        ("installation", {"status": "removed"}),
        ("listing", {"status": "revoked"}),
    ],
)
async def test_any_stale_component_fails_closed(
    db_session, chain, field, value
) -> None:
    """五个组件任一状态漂移，都必须拒绝调用，而不是「尽量跑」。"""
    obj = chain[field]
    for key, val in value.items():
        setattr(obj, key, val)
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor is not None, "执行器仍要返回，由 policy_error 表达拒绝"
    assert executor.policy_error == "current_state_unavailable"


async def test_snapshot_permissions_must_be_a_subset_of_the_grant(
    db_session, chain
) -> None:
    """授权被收窄后，快照里的旧权限不能再被行使。"""
    chain["grant"].permission_snapshot = ["records.list"]
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error == "permissions_changed"


async def test_executor_only_carries_the_intersection(db_session, chain) -> None:
    """授权被放宽时，执行器拿到的仍只是快照里那份权限。

    这条曾经想验证「取交集」，但**反向验证时发现改不掉**：把 `&` 换成只用
    snapshot，23 条全绿。原因是上面的 subset 检查已经保证
    `snapshot ⊆ grant`，此时交集恒等于 snapshot——`&` 在语义上是冗余的防御。
    所以这里只能锁「多出来的 `records.write` 不会进入执行器」这个可观测事实，
    不去假装能测到 `&` 本身。
    """
    chain["grant"].permission_snapshot = ["records.read", "records.write"]
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error is None
    assert executor.permission_snapshot == frozenset({"records.read"})


async def test_auth_required_but_no_credential_fails_closed(db_session, chain) -> None:
    chain["server"].auth_mode = "static_bearer"
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error == "credential_unavailable"


async def test_revoked_credential_fails_closed(db_session, chain) -> None:
    chain["server"].auth_mode = "static_bearer"
    credential = ExternalCredential(
        server_id=chain["server"].id,
        scope_owner_id=chain["owner"].id,
        label="token",
        kind="bearer",
        header_name="Authorization",
        ciphertext=CredentialCipher(CIPHER_KEY).encrypt("secret-value").ciphertext,
        key_version="v1",
        status="revoked",
        created_by=chain["owner"].id,
    )
    db_session.add(credential)
    await db_session.flush()
    chain["snapshot"].credential_id = credential.id
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error == "credential_unavailable"


async def test_credential_from_another_owner_fails_closed(
    db_session, chain, make_user
) -> None:
    """凭据按 owner 隔离：拿到别人的 credential_id 也不能解密使用。"""
    other, _ = await make_user(role="member")
    chain["server"].auth_mode = "static_bearer"
    credential = ExternalCredential(
        server_id=chain["server"].id,
        scope_owner_id=other.id,
        label="token",
        kind="bearer",
        header_name="Authorization",
        ciphertext=CredentialCipher(CIPHER_KEY).encrypt("secret-value").ciphertext,
        key_version="v1",
        status="active",
        created_by=other.id,
    )
    db_session.add(credential)
    await db_session.flush()
    chain["snapshot"].credential_id = credential.id
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error == "credential_unavailable"


async def test_credential_from_another_server_fails_closed(db_session, chain) -> None:
    chain["server"].auth_mode = "static_bearer"
    other_server = ExternalToolServer(
        owner_id=chain["owner"].id,
        created_by=chain["owner"].id,
        name="Other Server",
        description="fixture",
        endpoint_url="https://9.9.9.9/mcp",
        normalized_endpoint_url="https://9.9.9.9/mcp",
        maintainer="Platform",
        auth_mode="none",
        status="published",
        current_validation_status="passed",
    )
    db_session.add(other_server)
    await db_session.flush()
    credential = ExternalCredential(
        server_id=other_server.id,
        scope_owner_id=chain["owner"].id,
        label="token",
        kind="bearer",
        header_name="Authorization",
        ciphertext=CredentialCipher(CIPHER_KEY).encrypt("secret-value").ciphertext,
        key_version="v1",
        status="active",
        created_by=chain["owner"].id,
    )
    db_session.add(credential)
    await db_session.flush()
    chain["snapshot"].credential_id = credential.id
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error == "credential_unavailable"


async def test_undecryptable_credential_fails_closed_instead_of_raising(
    db_session, chain
) -> None:
    """解密失败不能让整个 Run 炸掉——它只该让这一个工具不可用。"""
    chain["server"].auth_mode = "static_bearer"
    credential = ExternalCredential(
        server_id=chain["server"].id,
        scope_owner_id=chain["owner"].id,
        label="token",
        kind="bearer",
        header_name="Authorization",
        ciphertext="not-a-fernet-token-but-long-enough",
        key_version="v1",
        status="active",
        created_by=chain["owner"].id,
    )
    db_session.add(credential)
    await db_session.flush()
    chain["snapshot"].credential_id = credential.id
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error == "credential_unavailable"
    assert executor.headers == {}


async def test_active_credential_is_decrypted_into_headers(db_session, chain) -> None:
    """健康凭据路径：解密值要真的进 headers，否则请求会缺鉴权头。"""
    chain["server"].auth_mode = "static_bearer"
    cipher = CredentialCipher(CIPHER_KEY)
    credential = ExternalCredential(
        server_id=chain["server"].id,
        scope_owner_id=chain["owner"].id,
        label="token",
        kind="bearer",
        header_name="Authorization",
        ciphertext=cipher.encrypt("secret-value").ciphertext,
        key_version="v1",
        status="active",
        created_by=chain["owner"].id,
    )
    db_session.add(credential)
    await db_session.flush()
    chain["snapshot"].credential_id = credential.id
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error is None
    assert executor.headers == {"Authorization": "Bearer secret-value"}


async def test_api_key_kind_is_not_prefixed_with_bearer(db_session, chain) -> None:
    """两种 kind 的头格式不同：api_key 原样，bearer 加前缀。"""
    chain["server"].auth_mode = "static_api_key"
    cipher = CredentialCipher(CIPHER_KEY)
    credential = ExternalCredential(
        server_id=chain["server"].id,
        scope_owner_id=chain["owner"].id,
        label="key",
        kind="api_key",
        header_name="X-API-Key",
        ciphertext=cipher.encrypt("raw-key").ciphertext,
        key_version="v1",
        status="active",
        created_by=chain["owner"].id,
    )
    db_session.add(credential)
    await db_session.flush()
    chain["snapshot"].credential_id = credential.id
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.headers == {"X-API-Key": "raw-key"}


async def test_unreachable_endpoint_fails_closed(db_session, chain) -> None:
    """endpoint 指向元数据地址时必须拒绝：这是 SSRF 的主入口。"""
    chain["server"].normalized_endpoint_url = "http://127.0.0.1/ssrf"
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error == "endpoint_unavailable"


async def test_stdio_transport_skips_endpoint_validation(db_session, chain) -> None:
    """stdio 没有 URL，不能拿 endpoint 校验去卡它。"""
    chain["server"].transport = "stdio"
    chain["server"].command = "npx"
    chain["server"].args = ["-y", "some-mcp-server"]
    chain["server"].normalized_endpoint_url = "stdio://local"
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor.policy_error is None


async def test_installation_owned_by_someone_else_is_not_usable(
    db_session, chain, make_user
) -> None:
    """join 里的 owner 条件是安全边界：别人装的工具不能因为共享快照就被用。"""
    other, _ = await make_user(role="member")
    chain["installation"].owner_id = other.id
    await db_session.commit()

    executor = await _executor_for(db_session, chain)

    assert executor is None, "查不到行就不该生成执行器（fail closed 的最外一层）"


async def test_authorize_rechecks_state_at_call_time(db_session, chain) -> None:
    """构造时合法 ≠ 调用时合法：授权可能在 Run 执行期间被撤销。"""
    executor = await _executor_for(db_session, chain)
    assert await executor.authorize_call() is True

    chain["grant"].status = "revoked"
    await db_session.commit()

    assert await executor.authorize_call() is False


async def test_authorize_detects_endpoint_drift(db_session, chain) -> None:
    """endpoint 在两次调用之间被改走，复核必须发现。"""
    executor = await _executor_for(db_session, chain)
    assert await executor.authorize_call() is True

    chain["server"].normalized_endpoint_url = "https://1.2.3.4/elsewhere"
    await db_session.commit()

    assert await executor.authorize_call() is False


async def test_authorize_fails_when_permissions_narrowed(db_session, chain) -> None:
    executor = await _executor_for(db_session, chain)
    assert await executor.authorize_call() is True

    chain["grant"].permission_snapshot = ["records.list"]
    await db_session.commit()

    assert await executor.authorize_call() is False


async def test_authorize_fails_for_a_different_owner(
    db_session, chain, make_user
) -> None:
    """复核里也带 owner 判断，不能只信构造时那一次。

    必须换成**真实的另一个用户**，不能用假 id 字符串——`owner_id` 有外键，
    随便填一个 UUID 会先被数据库拦住，测试就永远到不了那行判权逻辑。
    """
    other, _ = await make_user(role="member")
    executor = await _executor_for(db_session, chain)
    assert await executor.authorize_call() is True

    chain["installation"].owner_id = other.id
    await db_session.commit()

    assert await executor.authorize_call() is False


async def test_authorize_returns_false_when_the_grant_disappeared(
    db_session, chain
) -> None:
    """授权行查不到时复核只回答 False，不能抛异常。

    `authorize_current` 里的查询以 `ExternalToolServer` 为起点逐级 inner join，
    任何一级断了 `.first()` 就返回 None。这里先删 **snapshot**（释放
    `grant_id` 那个 NOT NULL 外键）再删 grant。

    注意删 snapshot 本身测不到任何东西——`authorize_current` 根本不查它，
    闭包里拿的是构造时捕获的各个 id；真正的行为变化来自 grant 行消失。
    """
    executor = await _executor_for(db_session, chain)
    assert await executor.authorize_call() is True

    await db_session.delete(chain["snapshot"])
    await db_session.flush()
    await db_session.delete(chain["grant"])
    await db_session.commit()

    assert await executor.authorize_call() is False
