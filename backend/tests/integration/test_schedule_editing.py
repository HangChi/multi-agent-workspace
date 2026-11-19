"""调度规则编辑与 cron 预览的细节单测（015 覆盖率补齐）。

两类行为特别容易写错：

1. **cron 预览用 200 返回解析失败**，不是 4xx。因为这是「边输边提示」的输入框，
   输入到一半的表达式不是错误请求——用 4xx 会让前端在用户打字时弹红条。
2. **编辑规则的权限不是简单的 owner 判断**：管理员能看别人的规则但不能改，
   且对 member 必须回 404（不暴露存在性）。这三个分支的差异是有意为之，
   写反了要么泄露存在性，要么让管理员误删别人的定时任务。
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

SCHEDULES_URL = "/api/v1/schedules"
AGENTS_URL = "/api/v1/agents"


def _agent_payload() -> dict:
    return {
        "name": f"preview-agent-{uuid.uuid4().hex[:6]}",
        "description": "cron 预览用",
        "system_prompt": "you are a helpful assistant",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_steps": 10,
        "max_run_seconds": 1800,
        "tools": [],
        "cost_limit_cents": 10000,
    }


def _schedule_payload(target_id: str, **over) -> dict:
    payload = {
        "name": "工作日晨报",
        "description": "每个工作日汇总进度",
        "cron_expr": "0 9 * * 1-5",
        "timezone": "Asia/Shanghai",
        "template": {
            "title": "每日进度汇总",
            "description": "汇总昨日完成与阻塞",
            "expected_output": "markdown 摘要",
            "priority": "normal",
        },
        "target": {"type": "agent", "id": target_id, "strategy": None},
        "enabled": True,
    }
    payload.update(over)
    return payload


async def _agent(client, headers) -> dict:
    resp = await client.post(AGENTS_URL, headers=headers, json=_agent_payload())
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------- cron 预览 ----------


async def test_preview_returns_next_runs(client, make_headers):
    headers = await make_headers()
    resp = await client.post(
        f"{SCHEDULES_URL}/preview",
        headers=headers,
        json={"cron_expr": "0 9 * * *", "timezone": "UTC", "count": 3},
    )

    assert resp.status_code == 200
    runs = resp.json()["next_runs"]
    assert len(runs) == 3
    # 严格递增：同一时刻重复出现会让扫描任务反复触发同一分钟
    assert runs == sorted(runs)
    assert len(set(runs)) == 3


async def test_preview_invalid_cron_returns_200_with_message(client, make_headers):
    """边输边提示的场景：解析失败是 200 + error，不是 4xx。"""
    headers = await make_headers()
    resp = await client.post(
        f"{SCHEDULES_URL}/preview",
        headers=headers,
        json={"cron_expr": "0 9 * *", "timezone": "UTC", "count": 3},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["next_runs"] == []
    assert "5 个字段" in body["error"]


async def test_preview_requires_member_role(client, make_headers):
    resp = await client.post(
        f"{SCHEDULES_URL}/preview",
        headers=await make_headers(role="viewer"),
        json={"cron_expr": "0 9 * * *", "timezone": "UTC", "count": 1},
    )
    assert resp.status_code == 403


# ---------- 编辑权限 ----------


async def test_admin_cannot_modify_others_schedule(client, make_headers):
    """管理员能看别人的规则，但不能改/删——否则一次误操作会改掉别人的定时任务。"""
    owner_headers = await make_headers()
    agent = await _agent(client, owner_headers)
    created = await client.post(
        SCHEDULES_URL, headers=owner_headers, json=_schedule_payload(agent["id"])
    )
    assert created.status_code == 201
    schedule_id = created.json()["id"]

    admin_headers = await make_headers(role="admin")
    resp = await client.patch(
        f"{SCHEDULES_URL}/{schedule_id}", headers=admin_headers, json={"name": "改名"}
    )

    assert resp.status_code == 403


async def test_member_sees_404_for_others_schedule(client, make_headers):
    """非本人且非管理员 → 404（不暴露存在性）。"""
    owner_headers = await make_headers()
    agent = await _agent(client, owner_headers)
    created = await client.post(
        SCHEDULES_URL, headers=owner_headers, json=_schedule_payload(agent["id"])
    )
    schedule_id = created.json()["id"]

    resp = await client.patch(
        f"{SCHEDULES_URL}/{schedule_id}",
        headers=await make_headers(),
        json={"name": "改名"},
    )

    assert resp.status_code == 404


# ---------- 模板字段编辑 ----------


async def test_patch_template_fields_partially(client, make_headers):
    """模板只改传入的字段：没传的保持原值（部分更新语义）。"""
    headers = await make_headers()
    agent = await _agent(client, headers)
    created = await client.post(
        SCHEDULES_URL, headers=headers, json=_schedule_payload(agent["id"])
    )
    schedule_id = created.json()["id"]

    resp = await client.patch(
        f"{SCHEDULES_URL}/{schedule_id}",
        headers=headers,
        json={
            "template": {
                "title": "新的标题",
                "priority": "high",
            }
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["template"]["title"] == "新的标题"
    assert body["template"]["priority"] == "high"
    # 没传的字段保持原值
    assert body["template"]["expected_output"] == "markdown 摘要"


async def test_patch_description_can_be_cleared(client, make_headers):
    """空字符串表示清空（与「不传=不改」相对）。"""
    headers = await make_headers()
    agent = await _agent(client, headers)
    created = await client.post(
        SCHEDULES_URL, headers=headers, json=_schedule_payload(agent["id"])
    )
    schedule_id = created.json()["id"]

    resp = await client.patch(
        f"{SCHEDULES_URL}/{schedule_id}", headers=headers, json={"description": ""}
    )

    assert resp.status_code == 200
    assert resp.json()["description"] in (None, "")


# ---------- 列表过滤 ----------


async def test_list_filters_by_enabled(client, make_headers):
    headers = await make_headers()
    agent = await _agent(client, headers)
    enabled = await client.post(
        SCHEDULES_URL, headers=headers, json=_schedule_payload(agent["id"])
    )
    disabled = await client.post(
        SCHEDULES_URL,
        headers=headers,
        json=_schedule_payload(agent["id"], name="停用的规则", enabled=False),
    )
    assert enabled.status_code == 201 and disabled.status_code == 201

    only_enabled = await client.get(
        SCHEDULES_URL, headers=headers, params={"enabled": True, "page_size": 100}
    )
    only_disabled = await client.get(
        SCHEDULES_URL, headers=headers, params={"enabled": False, "page_size": 100}
    )

    enabled_ids = {item["id"] for item in only_enabled.json()["items"]}
    disabled_ids = {item["id"] for item in only_disabled.json()["items"]}
    assert enabled.json()["id"] in enabled_ids
    assert disabled.json()["id"] in disabled_ids
    assert enabled_ids.isdisjoint(disabled_ids)


async def test_triggers_empty_history(client, make_headers):
    """还没触发过的规则，历史接口返回空列表而不是 404。"""
    headers = await make_headers()
    agent = await _agent(client, headers)
    created = await client.post(
        SCHEDULES_URL, headers=headers, json=_schedule_payload(agent["id"])
    )
    schedule_id = created.json()["id"]

    resp = await client.get(f"{SCHEDULES_URL}/{schedule_id}/triggers", headers=headers)

    assert resp.status_code == 200
    assert resp.json()["items"] == []
