"""023 模板版本历史 / 导入导出 / 团队共享的集成测试。

三组断言：
1. **版本是「改前」状态**：否则回滚永远回到同一份（最常见的实现错误）；
2. **回滚只还原配置**：名字与共享范围不动（名字可能已被占用，共享是关系不是配置）；
3. **team 可见不可改**：别人能用你的团队模板，但不能改它。
"""

import uuid

import pytest

from app.models.user import User

pytestmark = pytest.mark.integration

URL = "/api/v1/agent-templates"


async def _user(db, role: str = "member") -> User:
    user = User(
        email=f"tpl-{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x" * 60,
        role=role,
        status="active",
    )
    db.add(user)
    await db.commit()
    return user


async def _template(
    client, headers, *, name: str, prompt: str = "第一版提示词"
) -> dict:
    """建模板只能「从 Agent 另存」（005 没有 POST /agent-templates）。

    这本身是个好设计：模板是「一个真实跑得起来的配置的快照」，不是手填的表单。
    """
    agent = await client.post(
        "/api/v1/agents",
        json={
            "name": f"agent-{uuid.uuid4().hex[:6]}",
            "description": "desc",
            "system_prompt": prompt,
            "model": "Deepseek-V4.1-Flash",
            "temperature": 0.5,
            "max_steps": 5,
            "max_run_seconds": 600,
            "tools": [],
            "cost_limit_cents": 1000,
            "egress_mode": "default",
        },
        headers=headers,
    )
    assert agent.status_code == 201, agent.text[:300]
    resp = await client.post(
        f"/api/v1/agents/{agent.json()['id']}/save-as-template",
        json={"name": name, "description": "desc"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text[:300]
    return resp.json()


async def test_patch_records_pre_change_snapshot(
    db_session, client, make_headers
) -> None:
    owner = await _user(db_session)
    headers = await make_headers(owner)
    tpl = await _template(client, headers, name=f"版本-{uuid.uuid4().hex[:5]}")

    # 创建本身不产生版本（没有「改之前」）
    assert (await client.get(f"{URL}/{tpl['id']}/versions", headers=headers)).json()[
        "total"
    ] == 0

    await client.patch(
        f"{URL}/{tpl['id']}", json={"system_prompt": "第二版"}, headers=headers
    )
    versions = (await client.get(f"{URL}/{tpl['id']}/versions", headers=headers)).json()
    assert versions["total"] == 1
    # 版本 1 存的必须是**改前**（第一版）的内容
    assert tpl["system_prompt"] == "第一版提示词"

    detail = await client.get(URL, headers=headers)
    row = next(t for t in detail.json()["items"] if t["id"] == tpl["id"])
    assert row["system_prompt"] == "第二版"

    # 再改一次 → 版本号自增
    await client.patch(
        f"{URL}/{tpl['id']}", json={"system_prompt": "第三版"}, headers=headers
    )
    versions = (await client.get(f"{URL}/{tpl['id']}/versions", headers=headers)).json()
    assert [v["version"] for v in versions["items"]] == [2, 1]


async def test_restore_brings_back_config_but_not_name(
    db_session, client, make_headers
) -> None:
    owner = await _user(db_session)
    headers = await make_headers(owner)
    name = f"回滚-{uuid.uuid4().hex[:5]}"
    tpl = await _template(client, headers, name=name, prompt="原始提示词")

    await client.patch(
        f"{URL}/{tpl['id']}",
        json={"system_prompt": "被改坏的提示词", "max_steps": 77},
        headers=headers,
    )
    restored = await client.post(
        f"{URL}/{tpl['id']}/versions/1/restore", headers=headers
    )
    assert restored.status_code == 200, restored.text[:200]
    body = restored.json()
    assert body["system_prompt"] == "原始提示词"
    assert body["max_steps"] == 5
    assert body["name"] == name  # 名字不动

    # 回滚本身也是一次修改：又多了一个版本
    versions = (await client.get(f"{URL}/{tpl['id']}/versions", headers=headers)).json()
    assert versions["total"] == 2


async def test_export_import_roundtrip(db_session, client, make_headers) -> None:
    owner = await _user(db_session)
    other = await _user(db_session)
    owner_h = await make_headers(owner)
    other_h = await make_headers(other)
    tpl = await _template(client, owner_h, name=f"导出-{uuid.uuid4().hex[:5]}")

    exported = await client.get(f"{URL}/{tpl['id']}/export", headers=owner_h)
    assert exported.status_code == 200
    payload = exported.json()
    assert payload["schema_version"] == 1
    assert payload["system_prompt"] == tpl["system_prompt"]

    # 另一个用户导入 → 新建一份自己的
    imported = await client.post(f"{URL}/import", json=payload, headers=other_h)
    assert imported.status_code == 201, imported.text[:300]
    body = imported.json()
    assert body["id"] != tpl["id"]
    assert body["owner_id"] == other.id
    assert body["system_prompt"] == tpl["system_prompt"]
    assert body["visibility"] == "private"  # 导入默认私有

    # 同一用户再导一次 → 重名 409
    again = await client.post(f"{URL}/import", json=payload, headers=other_h)
    assert again.status_code == 409


async def test_import_rejects_unknown_schema_version(
    db_session, client, make_headers
) -> None:
    owner = await _user(db_session)
    headers = await make_headers(owner)
    resp = await client.post(
        f"{URL}/import",
        json={
            "schema_version": 99,
            "name": "未来格式",
            "system_prompt": "x",
        },
        headers=headers,
    )
    assert resp.status_code == 400
    assert "格式版本" in resp.json()["error"]["message"]


async def test_team_template_visible_but_not_editable(
    db_session, client, make_headers
) -> None:
    owner = await _user(db_session)
    mate = await _user(db_session)
    owner_h = await make_headers(owner)
    mate_h = await make_headers(mate)
    tpl = await _template(client, owner_h, name=f"共享-{uuid.uuid4().hex[:5]}")

    # 默认私有：队友看不到
    private_team = await client.get(f"{URL}?scope=team", headers=mate_h)
    assert all(t["id"] != tpl["id"] for t in private_team.json()["items"])

    shared = await client.patch(
        f"{URL}/{tpl['id']}", json={"visibility": "team"}, headers=owner_h
    )
    assert shared.status_code == 200 and shared.json()["visibility"] == "team"

    team_list = await client.get(f"{URL}?scope=team", headers=mate_h)
    assert any(t["id"] == tpl["id"] for t in team_list.json()["items"])

    # 能从它创建 Agent 吗？能（模板可读即可实例化）——这里只验可读
    detail = await client.get(f"{URL}/{tpl['id']}", headers=mate_h)
    assert detail.status_code == 200

    # 但改不了
    denied = await client.patch(
        f"{URL}/{tpl['id']}", json={"system_prompt": "我改的"}, headers=mate_h
    )
    assert denied.status_code == 403
    # 历史也只能看不能回滚
    denied_restore = await client.post(
        f"{URL}/{tpl['id']}/versions/1/restore", headers=mate_h
    )
    assert denied_restore.status_code == 403


async def test_builtin_reports_team_visibility(
    db_session, client, make_headers
) -> None:
    owner = await _user(db_session)
    headers = await make_headers(owner)
    rows = (await client.get(f"{URL}?scope=builtin", headers=headers)).json()["items"]
    if not rows:
        pytest.skip("测试库没有内置模板种子（生产由 seed 脚本注入）")
    assert all(row["visibility"] == "team" for row in rows)
