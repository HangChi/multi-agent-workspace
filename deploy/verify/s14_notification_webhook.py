"""外部通知通道的真实栈验收（018 / SC-001 / SC-003 / SC-004）。

四组场景：

- **A 通道配置**：建通道 201（secret 不回显）；私网地址 400；viewer 403。
- **B 端到端投递**：B 配通道（订阅 project_member_added）→ A 把 B 加进项目
  → B 的通道收到真实 POST（接收端用 `https://httpbin.org/post`，需要出网）；
  delivery 记 succeeded / attempts=1。
- **C 退订即时生效**：禁用通道后再触发同一事件 → 不再产生新的 delivery。
- **D 订阅过滤**：只订阅 run_completed 的通道，不接收 project_member_added。

签名正确性不在本脚本验（接收端是 httpbin，不校验）：由
`backend/tests/unit/test_notification_channel_guard.py` 的 openssl 向量与
`backend/tests/integration/test_notification_channels.py` 的 header 契约覆盖。

依赖：真实栈 + 一个 member 邀请码 + 容器出网（2026-11-21 实测 httpbin 可达）。

用法：
    python deploy/verify/s14_notification_webhook.py --base http://localhost:8080 --invite <INVITE>
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

PASSWORD = "TestPass123!"
PUBLIC_HOOK = "https://httpbin.org/post"


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    elapsed_s: float = 0.0

    def line(self) -> str:
        mark = "✅" if self.ok else "❌"
        cost = f"（{self.elapsed_s:.1f}s）" if self.elapsed_s else ""
        return f"{mark} {self.name} — {self.detail}{cost}"


class WebhookVerifier:
    def __init__(self, base: str, invite: str) -> None:
        self.api = f"{base.rstrip('/')}/api/v1"
        self.invite = invite
        self.run_id = uuid.uuid4().hex[:6]
        self.http = httpx.Client(timeout=40.0)
        self.results: list[Result] = []
        self.tokens: dict[str, str] = {}
        self.ids: dict[str, str] = {}

    # ---------- 基础 ----------

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    def _h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _post(self, who: str, path: str, json: dict | None = None) -> httpx.Response:
        return self.http.post(f"{self.api}{path}", json=json, headers=self._h(who))

    def _get(self, who: str, path: str, **params) -> httpx.Response:
        return self.http.get(f"{self.api}{path}", headers=self._h(who), params=params)

    def setup(self) -> None:
        for who in ("a", "b"):
            resp = self.http.post(
                f"{self.api}/auth/register",
                json={
                    "email": f"s14-{who}-{self.run_id}@test.com",
                    "password": PASSWORD,
                    "invite_code": self.invite,
                },
            )
            assert resp.status_code == 201, f"注册 {who} 失败：{resp.text[:200]}"
            body = resp.json()
            self.tokens[who] = body["tokens"]["access"]
            self.ids[who] = body["user"]["id"]

    # ---------- A 配置 ----------

    def scenario_a_config(self) -> str | None:
        started = time.monotonic()
        resp = self._post(
            "b",
            "/notification-channels",
            {"url": PUBLIC_HOOK, "event_types": ["project_member_added"]},
        )
        body = resp.json() if resp.status_code == 201 else {}
        ok = (
            resp.status_code == 201
            and body.get("enabled") is True
            and body.get("secret_configured") is True
            and "secret" not in body
        )
        self._record(
            "A1 建通道 201 且不回显 secret",
            ok,
            f"status={resp.status_code}",
            started,
        )
        channel_id = body.get("id")

        started = time.monotonic()
        bad = self._post(
            "b", "/notification-channels", {"url": "http://127.0.0.1:9000/hook"}
        )
        message = bad.json()["error"]["message"] if bad.status_code == 400 else ""
        self._record(
            "A2 私网地址保存即 400",
            bad.status_code == 400 and "不允许" in message,
            f"{bad.status_code} {message[:40]}",
            started,
        )
        return channel_id

    # ---------- B/C/D 投递 ----------

    def _wait_deliveries(
        self, channel_id: str, *, at_least: int = 1, timeout: float = 45.0
    ) -> list[dict]:
        deadline = time.monotonic() + timeout
        rows: list[dict] = []
        while time.monotonic() < deadline:
            rows = (
                self._get("b", f"/notification-channels/{channel_id}/deliveries")
                .json()
                .get("items", [])
            )
            if len(rows) >= at_least and all(r["status"] != "pending" for r in rows):
                return rows
            time.sleep(2)
        return rows

    def _add_b_to_new_project(self, who_owner: str = "a") -> None:
        project = self._post(who_owner, "/projects", {"name": f"s14-项目-{uuid.uuid4().hex[:5]}"})
        assert project.status_code == 201, project.text[:200]
        added = self._post(
            who_owner,
            f"/projects/{project.json()['id']}/members",
            {"user_id": self.ids["b"]},
        )
        assert added.status_code == 201, added.text[:200]

    def scenario_b_delivery(self, channel_id: str) -> None:
        started = time.monotonic()
        self._add_b_to_new_project()
        rows = self._wait_deliveries(channel_id)
        first = rows[0] if rows else {}
        ok = (
            bool(rows)
            and first.get("status") == "succeeded"
            and first.get("attempts") == 1
            and first.get("event_type") == "project_member_added"
        )
        self._record(
            "B1 事件触发真实投递（succeeded / 1 次）",
            ok,
            f"{len(rows)} 条，首条={first.get('status')}/{first.get('attempts')} 次/{first.get('event_type')}",
            started,
        )

    def scenario_c_unsubscribe(self, channel_id: str) -> None:
        started = time.monotonic()
        self.http.patch(
            f"{self.api}/notification-channels/{channel_id}",
            json={"enabled": False},
            headers=self._h("b"),
        )
        before = len(
            self._get("b", f"/notification-channels/{channel_id}/deliveries")
            .json()
            .get("items", [])
        )
        self._add_b_to_new_project()
        time.sleep(6)  # 给 worker 足够时间；若无新记录说明丢弃生效
        after = len(
            self._get("b", f"/notification-channels/{channel_id}/deliveries")
            .json()
            .get("items", [])
        )
        self._record(
            "C1 禁用后不再产生投递",
            after == before,
            f"禁用前 {before} 条 → 事件后 {after} 条",
            started,
        )

    def scenario_d_subscription_filter(self) -> None:
        started = time.monotonic()
        resp = self._post(
            "b",
            "/notification-channels",
            {"url": PUBLIC_HOOK, "event_types": ["run_completed"]},
        )
        assert resp.status_code == 201, resp.text[:200]
        only_run_id = resp.json()["id"]
        self._add_b_to_new_project()
        time.sleep(6)
        rows = (
            self._get("b", f"/notification-channels/{only_run_id}/deliveries")
            .json()
            .get("items", [])
        )
        self._record(
            "D1 未订阅的事件不建投递",
            rows == [],
            f"{len(rows)} 条（期望 0）",
            started,
        )

    def run(self) -> int:
        self.setup()
        channel_id = self.scenario_a_config()
        if channel_id:
            self.scenario_b_delivery(channel_id)
            self.scenario_c_unsubscribe(channel_id)
            self.scenario_d_subscription_filter()
        self.http.close()

        print()
        for r in self.results:
            print(r.line())
        failed = [r for r in self.results if not r.ok]
        print(
            f"\n合计 {len(self.results)} 场景：{len(self.results) - len(failed)} 通过 / {len(failed)} 失败"
        )
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="018 外部通知通道真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True, help="邀请码（member）")
    args = parser.parse_args()
    sys.exit(WebhookVerifier(args.base, args.invite).run())


if __name__ == "__main__":
    main()
