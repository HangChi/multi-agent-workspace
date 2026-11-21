"""模板市场（026）的真实栈验收。

三组场景：

- **A 复制**：A 把模板设为 team → B 在 scope=team 看到 → 复制 → 得到自己的
  private 副本（名字带「（副本）」，配置一致）。
- **B 署名**：scope=team 的列表里能看到 owner_email。
- **C 边界**：B 复制自己的 → 409；B 不能复制 A 的 private（404）。

用法：
    python deploy/verify/s21_template_market.py --base http://localhost:8080 --invite <INVITE>
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

PASSWORD = "TestPass123!"


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


class MarketVerifier:
    def __init__(self, base: str, invite: str) -> None:
        self.api = f"{base.rstrip('/')}/api/v1"
        self.invite = invite
        self.run_id = uuid.uuid4().hex[:6]
        self.http = httpx.Client(timeout=30.0)
        self.results: list[Result] = []
        self.tokens: dict[str, str] = {}
        self.emails: dict[str, str] = {}

    def _h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _post(self, who: str, path: str, json: dict | None = None) -> httpx.Response:
        return self.http.post(f"{self.api}{path}", json=json, headers=self._h(who))

    def _get(self, who: str, path: str, **params) -> httpx.Response:
        return self.http.get(f"{self.api}{path}", headers=self._h(who), params=params)

    def _patch(self, who: str, path: str, json: dict) -> httpx.Response:
        return self.http.patch(f"{self.api}{path}", json=json, headers=self._h(who))

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    def setup(self) -> None:
        for who in ("owner", "mate"):
            email = f"s21-{who}-{self.run_id}@test.com"
            resp = self.http.post(
                f"{self.api}/auth/register",
                json={"email": email, "password": PASSWORD, "invite_code": self.invite},
            )
            assert resp.status_code == 201, resp.text[:200]
            self.tokens[who] = resp.json()["tokens"]["access"]
            self.emails[who] = email

    def _template(self, who: str) -> dict:
        agent = self._post(
            who,
            "/agents",
            {
                "name": f"s21-agent-{uuid.uuid4().hex[:6]}",
                "system_prompt": "市场验收模板",
                "model": "Deepseek-V4.1-Flash",
                "max_steps": 3,
                "cost_limit_cents": 500,
                "tools": [],
            },
        )
        assert agent.status_code == 201, agent.text[:200]
        tpl = self._post(
            who,
            f"/agents/{agent.json()['id']}/save-as-template",
            {"name": f"s21市场-{uuid.uuid4().hex[:5]}", "description": "共享给团队"},
        )
        assert tpl.status_code == 201, tpl.text[:200]
        return tpl.json()

    def run(self) -> int:
        self.setup()
        shared = self._template("owner")
        private = self._template("owner")
        self._patch("owner", f"/agent-templates/{shared['id']}", {"visibility": "team"})

        # A 复制
        started = time.monotonic()
        copied = self._post("mate", f"/agent-templates/{shared['id']}/duplicate")
        body = copied.json() if copied.status_code == 201 else {}
        ok = (
            copied.status_code == 201
            and body.get("id") != shared["id"]
            and body.get("visibility") == "private"
            and str(body.get("name", "")).endswith("（副本）")
            and body.get("system_prompt") == shared["system_prompt"]
        )
        self._record(
            "A1 复制 team 模板（private 副本，名字带「（副本）」）",
            ok,
            f"{copied.status_code} {body.get('name')}",
            started,
        )

        # B 署名
        started = time.monotonic()
        rows = self._get("mate", "/agent-templates", scope="team").json()["items"]
        row = next((r for r in rows if r["id"] == shared["id"]), None)
        self._record(
            "B1 团队列表带作者（owner_email）",
            row is not None and row.get("owner_email") == self.emails["owner"],
            f"{row and row.get('owner_email')}",
            started,
        )

        # C 边界
        started = time.monotonic()
        own = self._template("mate")
        dup_own = self._post("mate", f"/agent-templates/{own['id']}/duplicate")
        dup_private = self._post("mate", f"/agent-templates/{private['id']}/duplicate")
        self._record(
            "C1 复制自己的 409 / 复制别人的 private 404",
            dup_own.status_code == 409 and dup_private.status_code == 404,
            f"own={dup_own.status_code} private={dup_private.status_code}",
            started,
        )

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
    parser = argparse.ArgumentParser(description="026 模板市场真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True)
    args = parser.parse_args()
    sys.exit(MarketVerifier(args.base, args.invite).run())


if __name__ == "__main__":
    main()
