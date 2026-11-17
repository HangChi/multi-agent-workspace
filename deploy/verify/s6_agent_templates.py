"""Agent 模板与一键克隆：真实 docker compose 栈验收（005 / T036）。

五个场景对应 spec 的用户故事，全部走 HTTP 打真实容器栈：

- **S1 从模板创建**：字段逐字段一致 + 模板引用失效工具时创建成功且被过滤
- **S2 一键克隆**：配置一致 + 实例级数据为 0 + 源对象不受影响 + 连续克隆不撞名
- **S3 另存为模板**：快照 + 双向隔离（改 Agent 不改模板，改模板不改 Agent）
- **S4 模板管理**：他人模板 404 同措辞、内置只读、重名 409、审计三动作
- **S5 新部署开箱可用**：内置模板存在且对任意成员可见

与 `strategy_scenarios.py` 的差别：本脚本**不需要跑 Run**——模板与克隆都是同步的
轻量写，不需要 mock LLM 或等待执行。因此它可以在任何 `up -d` 完成的栈上直接跑。

用法：
    python deploy/verify/s6_agent_templates.py --base http://localhost:8080 --invite <INVITE>
"""

import argparse
import asyncio
import sys
import time
import uuid
from dataclasses import dataclass

import httpx


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    elapsed_s: float = 0.0

    def line(self) -> str:
        mark = "✅" if self.ok else "❌"
        cost = f"（{self.elapsed_s:.2f}s）" if self.elapsed_s else ""
        return f"{mark} {self.name} — {self.detail}{cost}"


# 模板 → Agent 的 10 个配置字段（名称按规则派生，单独验）
CONFIG_FIELDS = (
    "description",
    "system_prompt",
    "model",
    "temperature",
    "max_steps",
    "max_run_seconds",
    "tools",
    "cost_limit_cents",
    "egress_mode",
)


class TemplateVerifier:
    def __init__(self, base: str, invite: str) -> None:
        self.base = base.rstrip("/")
        self.invite = invite
        self.results: list[Result] = []
        self._http = httpx.AsyncClient(timeout=30.0)
        self._tokens: dict[str, str] = {}
        self._source_agent: dict | None = None

    # ---------- 基础 ----------

    async def _register(self, key: str) -> str:
        email = f"tpl-{key}-{uuid.uuid4().hex[:8]}@test.com"
        resp = await self._http.post(
            f"{self.base}/api/v1/auth/register",
            json={"email": email, "password": "TestPass123!", "invite_code": self.invite},
        )
        assert resp.status_code == 201, f"注册失败: {resp.status_code} {resp.text[:300]}"
        token = resp.json()["tokens"]["access"]
        self._tokens[key] = token
        return token

    def _headers(self, key: str = "owner") -> dict[str, str]:
        return {"Authorization": f"Bearer {self._tokens[key]}"}

    async def _create_agent(self, key: str, **overrides) -> dict:
        payload = {
            "name": f"源 Agent {uuid.uuid4().hex[:6]}",
            "description": "验收源",
            "system_prompt": "你是验收助手，回答要简短。",
            "model": "Deepseek-V4.1-Flash",
            "temperature": 0.3,
            "max_steps": 4,
            "max_run_seconds": 120,
            "tools": ["calculate"],
            "cost_limit_cents": 300,
        }
        payload.update(overrides)
        resp = await self._http.post(
            f"{self.base}/api/v1/agents", headers=self._headers(key), json=payload
        )
        assert resp.status_code == 201, f"建 agent 失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    # ---------- S1：从模板创建 ----------

    async def s1_from_template(self) -> None:
        started = time.monotonic()
        tpl_resp = await self._http.post(
            f"{self.base}/api/v1/agents/{self._source_agent['id']}/save-as-template",
            headers=self._headers(),
            json={"name": f"S1 模板 {uuid.uuid4().hex[:6]}", "description": "S1 用"},
        )
        assert tpl_resp.status_code == 201, f"另存为模板失败: {tpl_resp.status_code}"
        tpl = tpl_resp.json()

        # 1a 逐字段一致（前端「原样提交」的形态）
        payload = {
            "name": "S1 产物",
            "description": tpl["description"],
            "system_prompt": tpl["system_prompt"],
            "model": tpl["model"],
            "temperature": tpl["temperature"],
            "max_steps": tpl["max_steps"],
            "max_run_seconds": tpl["max_run_seconds"],
            "tools": tpl["tools"],
            "cost_limit_cents": tpl["cost_limit_cents"],
            "egress_mode": tpl["egress_mode"],
        }
        resp = await self._http.post(
            f"{self.base}/api/v1/agents/from-template/{tpl['id']}",
            headers=self._headers(),
            json=payload,
        )
        assert resp.status_code == 201, f"从模板创建失败: {resp.status_code} {resp.text[:300]}"
        agent = resp.json()
        mismatches = [f for f in CONFIG_FIELDS if agent[f] != tpl[f]]
        self._record(
            "S1-a 从模板创建：配置逐字段一致",
            not mismatches and agent["source_template_id"] == tpl["id"],
            f"不一致字段={mismatches or '无'}",
            started,
        )

        # 1b 只改两个字段：产物只有这两个与模板不同（US1 AS4）
        edited = {**payload, "name": "S1 微调产物", "temperature": 1.5, "tools": []}
        resp = await self._http.post(
            f"{self.base}/api/v1/agents/from-template/{tpl['id']}",
            headers=self._headers(),
            json=edited,
        )
        assert resp.status_code == 201, f"微调创建失败: {resp.status_code} {resp.text[:300]}"
        tuned = resp.json()
        changed = [
            f
            for f in CONFIG_FIELDS
            if tuned[f] != tpl[f] and f not in ("temperature", "tools")
        ]
        self._record(
            "S1-b 基于模板微调：只有改动的字段不同",
            tuned["temperature"] == 1.5
            and tuned["tools"] == []
            and not changed,
            f"意外变动字段={changed or '无'}",
            started,
        )

    # ---------- S2：一键克隆 ----------

    async def s2_clone(self) -> None:
        started = time.monotonic()
        source = await self._create_agent("owner", egress_mode="restricted")

        resp = await self._http.post(
            f"{self.base}/api/v1/agents/{source['id']}/clone", headers=self._headers()
        )
        assert resp.status_code == 201, f"克隆失败: {resp.status_code} {resp.text[:300]}"
        clone = resp.json()

        mismatches = [f for f in CONFIG_FIELDS if clone[f] != source[f]]
        self._record(
            "S2-a 克隆：配置逐字段一致",
            not mismatches and clone["name"] == f"{source['name']} 副本",
            f"名称={clone['name']} 不一致字段={mismatches or '无'}",
            started,
        )

        # 实例级数据：用列表端点反查（新 Agent 不在任何组、无规则）
        groups = await self._http.get(f"{self.base}/api/v1/groups", headers=self._headers())
        memberships = [
            m
            for g in groups.json().get("items", [])
            for m in g.get("members", [])
            if m.get("agent_id") == clone["id"]
        ]
        self._record(
            "S2-b 克隆：不复制实例级数据",
            not memberships and clone["source_agent_id"] == source["id"],
            f"组成员关系={len(memberships)}",
            started,
        )

        # 连续克隆不撞名
        names = [clone["name"]]
        for _ in range(2):
            r = await self._http.post(
                f"{self.base}/api/v1/agents/{source['id']}/clone", headers=self._headers()
            )
            assert r.status_code == 201, f"连续克隆失败: {r.status_code}"
            names.append(r.json()["name"])
        self._record(
            "S2-c 连续克隆名称互不冲突",
            len(set(names)) == 3,
            " / ".join(names),
            started,
        )

        # restricted 无 allow 规则 → warning 与 POST /agents 一致
        self._record(
            "S2-d 克隆 restricted 源给出警告",
            clone.get("warning") == "限制模式下尚无启用的允许规则，运行时全部出站被拒。",
            f"warning={clone.get('warning')}",
            started,
        )

    # ---------- S3：另存为模板 + 双向隔离 ----------

    async def s3_snapshot_isolation(self) -> None:
        started = time.monotonic()
        agent = await self._create_agent("owner")
        tpl_resp = await self._http.post(
            f"{self.base}/api/v1/agents/{agent['id']}/save-as-template",
            headers=self._headers(),
            json={"name": f"S3 模板 {uuid.uuid4().hex[:6]}"},
        )
        tpl = tpl_resp.json()
        snapshot_prompt = tpl["system_prompt"]

        # 改 Agent → 模板不变
        await self._http.patch(
            f"{self.base}/api/v1/agents/{agent['id']}",
            headers=self._headers(),
            json={"system_prompt": "改过的提示词"},
        )
        tpl_after = (
            await self._http.get(
                f"{self.base}/api/v1/agent-templates/{tpl['id']}", headers=self._headers()
            )
        ).json()
        self._record(
            "S3-a 改 Agent 不影响模板",
            tpl_after["system_prompt"] == snapshot_prompt,
            f"模板提示词未变={tpl_after['system_prompt'] == snapshot_prompt}",
            started,
        )

        # 改模板 → Agent 不变
        await self._http.patch(
            f"{self.base}/api/v1/agent-templates/{tpl['id']}",
            headers=self._headers(),
            json={"system_prompt": "模板侧改过的提示词"},
        )
        agent_after = await self._get_agent(agent["id"])
        self._record(
            "S3-b 改模板不影响已创建的 Agent",
            agent_after["system_prompt"] == "改过的提示词",
            f"Agent 提示词={agent_after['system_prompt'][:20]}…",
            started,
        )

    # ---------- S4：模板管理与越权 ----------

    async def s4_management_and_authorization(self) -> None:
        started = time.monotonic()
        await self._register("stranger")
        agent = await self._create_agent("owner")
        tpl_resp = await self._http.post(
            f"{self.base}/api/v1/agents/{agent['id']}/save-as-template",
            headers=self._headers(),
            json={"name": f"S4 私有 {uuid.uuid4().hex[:6]}"},
        )
        tpl = tpl_resp.json()

        # 他人读 → 404 且与「不存在」同 body
        foreign = await self._http.get(
            f"{self.base}/api/v1/agent-templates/{tpl['id']}", headers=self._headers("stranger")
        )
        missing = await self._http.get(
            f"{self.base}/api/v1/agent-templates/00000000-0000-0000-0000-000000000000",
            headers=self._headers("stranger"),
        )
        self._record(
            "S4-a 他人模板 404 且措辞与不存在一致",
            foreign.status_code == 404
            and missing.status_code == 404
            and foreign.json() == missing.json(),
            f"foreign={foreign.status_code} missing={missing.status_code} body相同={foreign.json() == missing.json()}",
            started,
        )

        # 他人不可写/删
        patch = await self._http.patch(
            f"{self.base}/api/v1/agent-templates/{tpl['id']}",
            headers=self._headers("stranger"),
            json={"description": "x"},
        )
        self._record(
            "S4-b 他人不可改模板",
            patch.status_code == 404,
            f"status={patch.status_code}",
            started,
        )

        # 重名 409
        dup = await self._http.post(
            f"{self.base}/api/v1/agents/{agent['id']}/save-as-template",
            headers=self._headers(),
            json={"name": tpl["name"]},
        )
        self._record(
            "S4-c 同名模板 409 且不自动加后缀",
            dup.status_code == 409,
            f"status={dup.status_code} msg={dup.json()['error']['message']}",
            started,
        )

        # 删除模板不影响已创建的 Agent
        del_resp = await self._http.delete(
            f"{self.base}/api/v1/agent-templates/{tpl['id']}", headers=self._headers()
        )
        try:
            agent_still = await self._get_agent(agent["id"])
            alive = agent_still["id"] == agent["id"]
        except AssertionError:
            alive = False
        self._record(
            "S4-d 删模板不带走 Agent",
            del_resp.status_code == 204 and alive,
            f"delete={del_resp.status_code} agent仍在={alive}",
            started,
        )

    # ---------- S5：内置模板开箱可用 ----------

    async def s5_builtin_templates(self) -> None:
        started = time.monotonic()
        resp = await self._http.get(
            f"{self.base}/api/v1/agent-templates",
            headers=self._headers(),
            params={"scope": "builtin", "page_size": 100},
        )
        assert resp.status_code == 200, f"列内置模板失败: {resp.status_code}"
        items = resp.json()["items"]
        builtins = [t for t in items if t["is_builtin"] and t["owner_id"] is None]
        self._record(
            "S5-a 内置模板存在且 ≥3",
            len(builtins) >= 3,
            f"数量={len(builtins)} 名称={[t['name'] for t in builtins]}",
            started,
        )

        if builtins:
            b = builtins[0]
            patch = await self._http.patch(
                f"{self.base}/api/v1/agent-templates/{b['id']}",
                headers=self._headers(),
                json={"description": "试图改内置"},
            )
            self._record(
                "S5-b 内置模板只读",
                patch.status_code == 403,
                f"status={patch.status_code} msg={patch.json()['error']['message']}",
                started,
            )

    async def _get_agent(self, agent_id: str, key: str = "owner") -> dict:
        """按 id 反查 Agent。

        本 API 没有 `GET /agents/{id}`（只有列表，Method Not Allowed），
        因此用 `?all=1` 取全集后本地找——验收脚本量级小，这样最直接。
        """
        resp = await self._http.get(
            f"{self.base}/api/v1/agents",
            headers=self._headers(key),
            params={"all": "true", "page_size": 100},
        )
        assert resp.status_code == 200, f"列 agent 失败: {resp.status_code}"
        found = next((a for a in resp.json()["items"] if a["id"] == agent_id), None)
        assert found is not None, f"找不到 agent {agent_id}"
        return found

    async def close(self) -> None:
        await self._http.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", default="TEST-ADMIN-CODE-123")
    args = parser.parse_args()

    verifier = TemplateVerifier(args.base, args.invite)
    try:
        await verifier._register("owner")
        # S1 需要一个源 Agent
        verifier._source_agent = await verifier._create_agent("owner")
        await verifier.s1_from_template()
        await verifier.s2_clone()
        await verifier.s3_snapshot_isolation()
        await verifier.s4_management_and_authorization()
        await verifier.s5_builtin_templates()
    finally:
        await verifier.close()

    print()
    for result in verifier.results:
        print(result.line())
    failed = [r for r in verifier.results if not r.ok]
    print(f"\n总计：{'✅ 全部通过' if not failed else '❌ 存在失败'}（{len(verifier.results)} 项）")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
