"""模板版本 / 导入导出 / 团队共享（023）的真实栈验收。

三组场景：

- **A 版本历史**：PATCH 一次 → 出现 v1（内容是改前）；回滚 v1 → 配置回到改前。
- **B 导入导出**：导出 JSON → 另一账号导入 → 拿到自己的副本（内容一致）。
- **C 团队共享**：设为 team → 队友 scope=team 可见、详情 200、PATCH 403。

模板只能「从 Agent 另存」（005 没有 POST /agent-templates），脚本照此建。

用法：
    python deploy/verify/s19_template_sharing.py --base http://localhost:8080 --invite <INVITE>
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

PASSWORD = "TestPass123!"
API = "/api/v1"


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


class TemplateVerifier:
    def __init__(self, base: str, invite: str) -> None:
        self.api = f"{base.rstrip('/')}{API}"
        self.invite = invite
        self.run_id = uuid.uuid4().hex[:6]
        self.http = httpx.Client(timeout=30.0)
        self.results: list[Result] = []
        self.tokens: dict[str, str] = {}

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
            resp = self.http.post(
                f"{self.api}/auth/register",
                json={
                    "email": f"s19-{who}-{self.run_id}@test.com",
                    "password": PASSWORD,
                    "invite_code": self.invite,
                },
            )
            assert resp.status_code == 201, resp.text[:200]
            self.tokens[who] = resp.json()["tokens"]["access"]

    def _template(self, who: str, prompt: str = "第一版提示词") -> dict:
        agent = self._post(
            who,
            "/agents",
            {
                "name": f"s19-agent-{uuid.uuid4().hex[:6]}",
                "system_prompt": prompt,
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
            {"name": f"s19模板-{uuid.uuid4().hex[:5]}", "description": "验收"},
        )
        assert tpl.status_code == 201, tpl.text[:200]
        return tpl.json()

    def run(self) -> int:
        self.setup()
        tpl = self._template("owner", "原始提示词")

        # A 版本历史
        started = time.monotonic()
        self._patch(
            "owner",
            f"/agent-templates/{tpl['id']}",
            {"system_prompt": "改坏的提示词"},
        )
        versions = self._get(
            "owner", f"/agent-templates/{tpl['id']}/versions"
        ).json()
        restored = self._post(
            "owner", f"/agent-templates/{tpl['id']}/versions/1/restore"
        )
        body = restored.json() if restored.status_code == 200 else {}
        self._record(
            "A1 版本记录（改前快照）+ 回滚生效",
            versions["total"] == 1
            and restored.status_code == 200
            and body.get("system_prompt") == "原始提示词",
            f"versions={versions['total']} restored={body.get('system_prompt')!r}",
            started,
        )

        # B 导出 → 另一账号导入
        started = time.monotonic()
        exported = self._get("owner", f"/agent-templates/{tpl['id']}/export")
        payload = exported.json() if exported.status_code == 200 else {}
        imported = self._post("mate", "/agent-templates/import", payload)
        imported_body = imported.json() if imported.status_code == 201 else {}
        ok = (
            exported.status_code == 200
            and payload.get("schema_version") == 1
            and imported.status_code == 201
            and imported_body.get("system_prompt") == tpl["system_prompt"]
            and imported_body.get("id") != tpl["id"]
        )
        self._record(
            "B1 导出后另一账号可导入（得到独立副本）",
            ok,
            f"export={exported.status_code} import={imported.status_code}",
            started,
        )

        # C 团队共享：可见不可改
        started = time.monotonic()
        shared = self._patch(
            "owner", f"/agent-templates/{tpl['id']}", {"visibility": "team"}
        )
        team_list = self._get("mate", "/agent-templates", scope="team").json()["items"]
        visible = any(t["id"] == tpl["id"] for t in team_list)
        denied = self._patch(
            "mate", f"/agent-templates/{tpl['id']}", {"system_prompt": "我改的"}
        )
        self._record(
            "C1 team 共享可见但队友改不了（403）",
            shared.status_code == 200 and visible and denied.status_code == 403,
            f"share={shared.status_code} visible={visible} patch={denied.status_code}",
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
    parser = argparse.ArgumentParser(description="023 模板共享/版本真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True)
    args = parser.parse_args()
    sys.exit(TemplateVerifier(args.base, args.invite).run())


if __name__ == "__main__":
    main()
