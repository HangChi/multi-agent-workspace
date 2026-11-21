"""依赖图谱与自动启动配置（021）的真实栈验收。

四组场景：

- **A 图谱**：A←B←C 链，`GET /requests/A/dependency-graph` 返回
  topo_order=[A,B,C]、critical_path=[A,B,C]、B/C 各有 1 个未完成前置。
- **B 自动启动配置**：创建者 PATCH `auto_start=true + auto_agent_id` → 200 且回读一致。
- **C 权限**：项目内 editor 成员改自动启动 → 403（会花钱的配置与「改归属」同档）。
- **D 读权限**：陌生人读图谱 → 404。

**不在这里验「自动启动真的跑起来」**：那要求前置需求真的执行完成（LLM 调用 +
worker），成本与不确定性都高。任务体（`app/tasks/auto_start_request.py`）的三道
幂等校验由代码与集成测试覆盖；端到端手工步骤见 `specs/021-dependency-graph/quickstart.md`。

用法：
    python deploy/verify/s17_dependency_graph.py --base http://localhost:8080 --invite <INVITE>
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


class GraphVerifier:
    def __init__(self, base: str, invite: str) -> None:
        self.api = f"{base.rstrip('/')}/api/v1"
        self.invite = invite
        self.run_id = uuid.uuid4().hex[:6]
        self.http = httpx.Client(timeout=30.0)
        self.results: list[Result] = []
        self.tokens: dict[str, str] = {}
        self.ids: dict[str, str] = {}

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    def _h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _post(self, who: str, path: str, json: dict | None = None) -> httpx.Response:
        return self.http.post(f"{self.api}{path}", json=json, headers=self._h(who))

    def _patch(self, who: str, path: str, json: dict) -> httpx.Response:
        return self.http.patch(f"{self.api}{path}", json=json, headers=self._h(who))

    def _get(self, who: str, path: str, **params) -> httpx.Response:
        return self.http.get(f"{self.api}{path}", headers=self._h(who), params=params)

    def setup(self) -> None:
        for who in ("owner", "mate", "stranger"):
            resp = self.http.post(
                f"{self.api}/auth/register",
                json={
                    "email": f"s17-{who}-{self.run_id}@test.com",
                    "password": PASSWORD,
                    "invite_code": self.invite,
                },
            )
            assert resp.status_code == 201, resp.text[:200]
            body = resp.json()
            self.tokens[who] = body["tokens"]["access"]
            self.ids[who] = body["user"]["id"]

    def run(self) -> int:
        self.setup()

        a = self._post("owner", "/requests", {"title": f"A 前置-{self.run_id}"}).json()
        b = self._post("owner", "/requests", {"title": f"B 中间-{self.run_id}"}).json()
        c = self._post("owner", "/requests", {"title": f"C 收尾-{self.run_id}"}).json()
        assert self._post(
            "owner", f"/requests/{b['id']}/dependencies", {"dependency_id": a["id"]}
        ).status_code == 201
        assert self._post(
            "owner", f"/requests/{c['id']}/dependencies", {"dependency_id": b["id"]}
        ).status_code == 201

        # A 图谱
        started = time.monotonic()
        resp = self._get("owner", f"/requests/{a['id']}/dependency-graph")
        body = resp.json() if resp.status_code == 200 else {}
        ids = [n["id"] for n in body.get("nodes", [])]
        unmet = {n["id"]: n["unmet_count"] for n in body.get("nodes", [])}
        ok = (
            resp.status_code == 200
            and ids == [a["id"], b["id"], c["id"]]
            and body.get("topo_order") == [a["id"], b["id"], c["id"]]
            and body.get("critical_path") == [a["id"], b["id"], c["id"]]
            and unmet.get(b["id"]) == 1
            and unmet.get(c["id"]) == 1
        )
        self._record(
            "A1 图谱：拓扑序 + 关键路径 + 未完成前置计数",
            ok,
            f"topo={len(body.get('topo_order', []))} critical={len(body.get('critical_path', []))}",
            started,
        )

        # B 自动启动配置
        started = time.monotonic()
        agent = self._post(
            "owner",
            "/agents",
            {
                "name": f"s17-agent-{self.run_id}",
                "system_prompt": "x",
                "model": "Deepseek-V4.1-Flash",
                "max_steps": 2,
                "cost_limit_cents": 1000,
                "tools": [],
            },
        ).json()
        patched = self._patch(
            "owner",
            f"/requests/{c['id']}",
            {"auto_start": True, "auto_agent_id": agent["id"]},
        )
        detail = self._get("owner", f"/requests/{c['id']}").json()
        ok = (
            patched.status_code == 200
            and detail["auto_start"] is True
            and detail["auto_agent_id"] == agent["id"]
        )
        self._record(
            "B1 创建者可设置自动启动并回读一致",
            ok,
            f"status={patched.status_code}",
            started,
        )

        # C 权限：项目 editor 成员不能改自动启动
        started = time.monotonic()
        project = self._post(
            "owner", "/projects", {"name": f"s17项目-{self.run_id}"}
        ).json()
        project_req = self._post(
            "owner",
            "/requests",
            {"title": f"项目内-{self.run_id}", "project_id": project["id"]},
        ).json()
        self._post(
            "owner",
            f"/projects/{project['id']}/members",
            {"user_id": self.ids["mate"]},
        )
        denied = self._patch(
            "mate", f"/requests/{project_req['id']}", {"auto_start": True}
        )
        self._record(
            "C1 项目成员（editor）改自动启动 403",
            denied.status_code == 403,
            f"{denied.status_code}",
            started,
        )

        # D 陌生人读图谱 404
        started = time.monotonic()
        hidden = self._get("stranger", f"/requests/{a['id']}/dependency-graph")
        self._record(
            "D1 陌生人读图谱 404",
            hidden.status_code == 404,
            str(hidden.status_code),
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
    parser = argparse.ArgumentParser(description="021 依赖图谱真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True)
    args = parser.parse_args()
    sys.exit(GraphVerifier(args.base, args.invite).run())


if __name__ == "__main__":
    main()
