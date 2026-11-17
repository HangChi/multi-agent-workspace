"""调度规则 CRUD 契约测试（T014/T015/T016，US1）。

对着 contracts/api.md §1 逐条断言，三组关注点分开：

- `TestPermissionMatrix`：权限矩阵。最容易写错的两处是「admin 可读不可写」
  与「越权与不存在同为 404」——后者不是风格问题，用 403 区分「存在但无权」
  会让任何人可以枚举他人的调度 id。
- `TestValidationOrder`：六步校验**顺序**。顺序本身是契约：错误必须指向
  用户该先改的那一处。若把目标校验提到 cron 之前，同时错两处时用户会先被
  告知「目标不存在」，改完目标才发现表达式也是错的。
- `TestNextRunRecompute`：`next_run_at` 何时重算。这里最险的是「重新启用」：
  若从旧的 `next_run_at` 继续推进，一个停了 3 天的规则会在启用瞬间
  补跑一次（用户看到的是"我刚打开它就自己跑了一遍"）。
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.db import SessionLocal

SCHEDULES_URL = "/api/v1/schedules"
AGENTS_URL = "/api/v1/agents"
GROUPS_URL = "/api/v1/groups"


def _agent_payload(**over) -> dict:
    payload = {
        "name": "sched-target-agent",
        "description": "target for schedule tests",
        "system_prompt": "you are a helpful assistant",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_steps": 10,
        "max_run_seconds": 1800,
        "tools": [],
        "cost_limit_cents": 10000,
    }
    payload.update(over)
    return payload


async def _make_agent(client, headers: dict, **over) -> dict:
    resp = await client.post(AGENTS_URL, headers=headers, json=_agent_payload(**over))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_group(client, headers: dict) -> dict:
    resp = await client.post(
        GROUPS_URL,
        headers=headers,
        json={"name": "sched-target-group", "routing_strategy": "fan_out"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


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


async def _create_schedule(client, headers: dict, target_id: str, **over) -> dict:
    resp = await client.post(
        SCHEDULES_URL, headers=headers, json=_schedule_payload(target_id, **over)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------- T014 权限矩阵 ----------


class TestPermissionMatrix:
    async def test_viewer_is_forbidden_on_every_endpoint(self, client, make_headers):
        member_h = await make_headers(role="member")
        agent = await _make_agent(client, member_h)
        sched = await _create_schedule(client, member_h, agent["id"])

        viewer_h = await make_headers(role="viewer")
        assert (await client.get(SCHEDULES_URL, headers=viewer_h)).status_code == 403
        assert (
            await client.post(
                SCHEDULES_URL, headers=viewer_h, json=_schedule_payload(agent["id"])
            )
        ).status_code == 403
        assert (
            await client.get(f"{SCHEDULES_URL}/{sched['id']}", headers=viewer_h)
        ).status_code == 403
        assert (
            await client.patch(
                f"{SCHEDULES_URL}/{sched['id']}", headers=viewer_h, json={"name": "x"}
            )
        ).status_code == 403
        assert (
            await client.delete(f"{SCHEDULES_URL}/{sched['id']}", headers=viewer_h)
        ).status_code == 403

    async def test_member_can_crud_own_schedule(self, client, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        sched = await _create_schedule(client, h, agent["id"])

        assert (
            await client.get(f"{SCHEDULES_URL}/{sched['id']}", headers=h)
        ).status_code == 200
        assert (
            await client.patch(
                f"{SCHEDULES_URL}/{sched['id']}", headers=h, json={"name": "改名"}
            )
        ).status_code == 200
        assert (
            await client.delete(f"{SCHEDULES_URL}/{sched['id']}", headers=h)
        ).status_code == 204

    async def test_member_list_only_contains_own(self, client, make_headers):
        h1 = await make_headers(role="member")
        h2 = await make_headers(role="member")
        a1 = await _make_agent(client, h1)
        a2 = await _make_agent(client, h2)
        s1 = await _create_schedule(client, h1, a1["id"], name="我的规则")
        await _create_schedule(client, h2, a2["id"], name="别人的规则")

        body = (await client.get(SCHEDULES_URL, headers=h1)).json()
        ids = {i["id"] for i in body["items"]}
        assert s1["id"] in ids
        assert len(ids) == 1

    async def test_admin_can_read_everything(self, client, make_headers):
        mh = await make_headers(role="member")
        agent = await _make_agent(client, mh)
        sched = await _create_schedule(client, mh, agent["id"])

        ah = await make_headers(role="admin")
        listing = await client.get(SCHEDULES_URL, headers=ah)
        assert listing.status_code == 200
        assert sched["id"] in {i["id"] for i in listing.json()["items"]}
        assert (
            await client.get(f"{SCHEDULES_URL}/{sched['id']}", headers=ah)
        ).status_code == 200

    @pytest.mark.xfail(
        reason="GET /schedules/{id}/triggers 属 Phase 7（T042-T048），届时转绿",
        strict=True,
    )
    async def test_viewer_forbidden_on_triggers_endpoint(self, client, make_headers):
        member_h = await make_headers(role="member")
        agent = await _make_agent(client, member_h)
        sched = await _create_schedule(client, member_h, agent["id"])
        viewer_h = await make_headers(role="viewer")
        resp = await client.get(
            f"{SCHEDULES_URL}/{sched['id']}/triggers", headers=viewer_h
        )
        assert resp.status_code == 403

    @pytest.mark.xfail(
        reason="GET /schedules/{id}/triggers 属 Phase 7（T042-T048），届时转绿",
        strict=True,
    )
    async def test_admin_can_read_triggers(self, client, make_headers):
        mh = await make_headers(role="member")
        agent = await _make_agent(client, mh)
        sched = await _create_schedule(client, mh, agent["id"])
        ah = await make_headers(role="admin")
        resp = await client.get(f"{SCHEDULES_URL}/{sched['id']}/triggers", headers=ah)
        assert resp.status_code == 200

    async def test_admin_cannot_write_others_schedule(self, client, make_headers):
        """admin 可读不可写是 FR-005 的刻意取舍，不是遗漏。

        admin 的职责是排障，不是替用户调度生产任务——改坏后责任与成本
        都落在创建者身上。
        """
        mh = await make_headers(role="member")
        agent = await _make_agent(client, mh)
        sched = await _create_schedule(client, mh, agent["id"])

        ah = await make_headers(role="admin")
        assert (
            await client.patch(
                f"{SCHEDULES_URL}/{sched['id']}", headers=ah, json={"name": "admin 改"}
            )
        ).status_code == 403
        assert (
            await client.delete(f"{SCHEDULES_URL}/{sched['id']}", headers=ah)
        ).status_code == 403

    async def test_cross_user_get_returns_404_not_403(self, client, make_headers):
        """越权与不存在必须同响应 404。

        若返回 403，攻击者可用状态码差异枚举他人的调度 id：
        403 = 存在，404 = 不存在。404 让"存在但无权"与"不存在"不可区分。
        """
        h1 = await make_headers(role="member")
        h2 = await make_headers(role="member")
        a1 = await _make_agent(client, h1)
        sched = await _create_schedule(client, h1, a1["id"])

        assert (
            await client.get(f"{SCHEDULES_URL}/{sched['id']}", headers=h2)
        ).status_code == 404
        assert (
            await client.patch(
                f"{SCHEDULES_URL}/{sched['id']}", headers=h2, json={"name": "x"}
            )
        ).status_code == 404
        assert (
            await client.delete(f"{SCHEDULES_URL}/{sched['id']}", headers=h2)
        ).status_code == 404
        assert (
            await client.get(f"{SCHEDULES_URL}/{sched['id']}/triggers", headers=h2)
        ).status_code == 404

    async def test_unknown_id_and_foreign_id_have_identical_response(
        self, client, make_headers
    ):
        """两条路径的响应体也必须一致，否则仍可区分。"""
        h1 = await make_headers(role="member")
        h2 = await make_headers(role="member")
        a1 = await _make_agent(client, h1)
        sched = await _create_schedule(client, h1, a1["id"])

        foreign = (
            await client.get(f"{SCHEDULES_URL}/{sched['id']}", headers=h2)
        ).json()
        unknown = (
            await client.get(
                f"{SCHEDULES_URL}/00000000-0000-0000-0000-000000000000", headers=h2
            )
        ).json()
        assert foreign == unknown


# ---------- T015 六步校验顺序 ----------


class TestValidationOrder:
    async def test_invalid_cron_and_bad_target_reports_cron_first(
        self, client, make_headers
    ):
        """同时错两处时，必须报 cron 的错误（第 3 步早于第 5 步）。

        这条是「顺序即契约」的核心断言：把目标校验提前的实现会在这里失败，
        而两个错误单独测都通不过——只有同时错才暴露顺序。
        """
        h = await make_headers(role="member")
        resp = await client.post(
            SCHEDULES_URL,
            headers=h,
            json=_schedule_payload(
                "00000000-0000-0000-0000-000000000000", cron_expr="99 99 * * *"
            ),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["detail"]["field"] == "cron_expr"

    async def test_invalid_cron_points_at_cron_expr_field(self, client, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        resp = await client.post(
            SCHEDULES_URL,
            headers=h,
            json=_schedule_payload(agent["id"], cron_expr="not a cron"),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "INVALID_INPUT"
        assert body["error"]["detail"]["field"] == "cron_expr"
        # 措辞不得泄漏 crontab / ValueError 等内部细节
        msg = body["error"]["message"]
        assert "crontab" not in msg.lower()
        assert "ValueError" not in msg

    async def test_never_occurring_cron_is_rejected(self, client, make_headers):
        """`0 0 31 2 *` 能解析但永远算不出下一次，必须拒。

        判据是「能算出下一时刻」而不是「能解析」——这是契约 §1 明确写出的。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        resp = await client.post(
            SCHEDULES_URL,
            headers=h,
            json=_schedule_payload(agent["id"], cron_expr="0 0 31 2 *"),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["detail"]["field"] == "cron_expr"

    async def test_unknown_timezone_points_at_timezone_field(
        self, client, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        resp = await client.post(
            SCHEDULES_URL,
            headers=h,
            json=_schedule_payload(agent["id"], timezone="Mars/Olympus"),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["detail"]["field"] == "timezone"

    async def test_shape_error_wins_over_cron_error(self, client, make_headers):
        """第 2 步（形状）早于第 3 步（cron）。"""
        h = await make_headers(role="member")
        resp = await client.post(
            SCHEDULES_URL,
            headers=h,
            json=_schedule_payload("x", name="", cron_expr="99 99 * * *"),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_unavailable_target_is_404(self, client, make_headers):
        h = await make_headers(role="member")
        resp = await client.post(
            SCHEDULES_URL,
            headers=h,
            json=_schedule_payload("00000000-0000-0000-0000-000000000000"),
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "NOT_FOUND"
        assert "Agent" in body["error"]["message"] or "组" in body["error"]["message"]

    async def test_other_users_agent_is_unavailable(self, client, make_headers):
        """别人的 Agent 对当前用户就是「不存在」，同样是 404 而非 403。

        先让 h2 用自己的 Agent 建成功一条，证明端点本身可用——
        否则路由不存在时的 404 会让这条断言假通过。
        """
        h1 = await make_headers(role="member")
        h2 = await make_headers(role="member")
        a1 = await _make_agent(client, h1)
        a2 = await _make_agent(client, h2)
        await _create_schedule(client, h2, a2["id"])

        resp = await client.post(
            SCHEDULES_URL, headers=h2, json=_schedule_payload(a1["id"])
        )
        assert resp.status_code == 404

    async def test_archived_agent_is_unavailable(self, client, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        # 先用同一个 agent 建成功一条，证明端点可用（防路由缺失导致的假 404）
        await _create_schedule(client, h, agent["id"])

        resp = await client.post(f"{AGENTS_URL}/{agent['id']}/archive", headers=h)
        assert resp.status_code in (200, 204), resp.text
        resp = await client.post(
            SCHEDULES_URL, headers=h, json=_schedule_payload(agent["id"])
        )
        assert resp.status_code == 404

    async def test_group_strategy_on_agent_is_400(self, client, make_headers):
        """第 6 步：strategy 与目标类型不相容 → 400（不是 422）。

        这是「两个合法值之间的相容性」问题，不是字段本身不合法。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        resp = await client.post(
            SCHEDULES_URL,
            headers=h,
            json=_schedule_payload(
                agent["id"],
                target={"type": "agent", "id": agent["id"], "strategy": "fan_out"},
            ),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_INPUT"

    async def test_group_target_with_strategy_is_accepted(self, client, make_headers):
        h = await make_headers(role="member")
        group = await _make_group(client, h)
        resp = await client.post(
            SCHEDULES_URL,
            headers=h,
            json=_schedule_payload(
                group["id"],
                target={"type": "group", "id": group["id"], "strategy": "fan_out"},
            ),
        )
        assert resp.status_code == 201, resp.text


# ---------- T016 next_run_at 重算 ----------


class TestNextRunRecompute:
    async def test_create_computes_next_run_at(self, client, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        sched = await _create_schedule(client, h, agent["id"])
        assert sched["next_run_at"] is not None
        assert sched["enabled"] is True

    async def test_changing_cron_recomputes(self, client, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        sched = await _create_schedule(client, h, agent["id"], cron_expr="0 9 * * *")
        before = sched["next_run_at"]
        resp = await client.patch(
            f"{SCHEDULES_URL}/{sched['id']}",
            headers=h,
            json={"cron_expr": "30 14 * * *"},
        )
        assert resp.status_code == 200
        after = resp.json()["next_run_at"]
        assert after != before
        # 新时间必须是 14:30 那一班（按 Shanghai 求值，返回 UTC）
        assert after.endswith(":30:00Z") or "T06:30:00" in after

    async def test_changing_timezone_recomputes(self, client, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        sched = await _create_schedule(client, h, agent["id"], cron_expr="0 9 * * *")
        before = sched["next_run_at"]
        resp = await client.patch(
            f"{SCHEDULES_URL}/{sched['id']}", headers=h, json={"timezone": "UTC"}
        )
        assert resp.status_code == 200
        # 9:00 UTC = 17:00 Shanghai，与原来的 01:00Z 不同
        assert resp.json()["next_run_at"] != before

    async def test_changing_template_does_not_recompute(self, client, make_headers):
        """模板与时间语义无关，改它不该挪动下次触发时间。"""
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        sched = await _create_schedule(client, h, agent["id"])
        before = sched["next_run_at"]
        resp = await client.patch(
            f"{SCHEDULES_URL}/{sched['id']}",
            headers=h,
            json={"template": {"title": "换了标题"}},
        )
        assert resp.status_code == 200
        assert resp.json()["next_run_at"] == before
        assert resp.json()["template"]["title"] == "换了标题"

    async def test_changing_target_does_not_recompute(self, client, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        other = await _make_agent(client, h, name="sched-target-agent-2")
        sched = await _create_schedule(client, h, agent["id"])
        before = sched["next_run_at"]
        resp = await client.patch(
            f"{SCHEDULES_URL}/{sched['id']}",
            headers=h,
            json={"target": {"type": "agent", "id": other["id"], "strategy": None}},
        )
        assert resp.status_code == 200
        assert resp.json()["next_run_at"] == before
        assert resp.json()["target"]["id"] == other["id"]

    async def test_disable_clears_next_run_at(self, client, make_headers):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        sched = await _create_schedule(client, h, agent["id"])
        resp = await client.patch(
            f"{SCHEDULES_URL}/{sched['id']}", headers=h, json={"enabled": False}
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["next_run_at"] is None

    async def test_reenable_recomputes_from_now_not_from_old_value(
        self, client, make_headers
    ):
        """重新启用必须**从现在**算，不能从旧的 next_run_at 继续。

        若沿用旧值，一个停了很久的规则会在启用瞬间补跑一次——
        用户看到的是「我刚打开它就自己跑了一遍」。这条断言把历史的
        陈旧程度拉到明显的过去，任何"从旧值继续"的实现都会算出过去时刻。
        """
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        sched = await _create_schedule(client, h, agent["id"])

        await client.patch(
            f"{SCHEDULES_URL}/{sched['id']}", headers=h, json={"enabled": False}
        )
        # 直接改库把 next_run_at 压到很久以前，模拟"停了很久"
        async with SessionLocal() as s:
            await s.execute(
                text(
                    "UPDATE schedules SET next_run_at = now() - interval '3 days' "
                    "WHERE id = :sid"
                ),
                {"sid": sched["id"]},
            )
            await s.commit()

        resp = await client.patch(
            f"{SCHEDULES_URL}/{sched['id']}", headers=h, json={"enabled": True}
        )
        assert resp.status_code == 200
        new_next = datetime.fromisoformat(resp.json()["next_run_at"])
        assert new_next > datetime.now(UTC), "重新启用的下次触发时间必须在未来"

    async def test_disable_then_enable_via_patch_keeps_other_fields(
        self, client, make_headers
    ):
        h = await make_headers(role="member")
        agent = await _make_agent(client, h)
        sched = await _create_schedule(client, h, agent["id"])
        resp = await client.patch(
            f"{SCHEDULES_URL}/{sched['id']}", headers=h, json={"enabled": False}
        )
        body = resp.json()
        assert body["name"] == sched["name"]
        assert body["cron_expr"] == sched["cron_expr"]
        assert body["description"] == sched["description"]
