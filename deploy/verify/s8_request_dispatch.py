"""需求分派与收件箱的真实栈验收（007 / T036）。

八个场景对应 spec 的成功标准，全部走 HTTP 打真实容器栈（三个账号：
owner / worker / other，覆盖「创建者/接收人/陌生人」三种身份）：

- **A 分派闭环**（SC-001）：owner 建需求 → assign(user) → 202 pending；
  worker 的 relevant 列表含该需求；未读 +1；受理耗时 < 100ms。
- **B 接收**（AC-1/AC-3）：worker accept → 202 accepted + owner 收到
  request_accepted 通知；other accept → 404。
- **C 退回**（RJ-1/RJ-3）：worker 附原因 reject → 202 created + 三列清空 +
  owner 收到含原因通知；空原因 → 422。
- **D 转交**（A-2）：pending 再拖给第三人 → 历史 dispatch+reassign 两条 +
  旧接收人收到转交通知。
- **E 执行与配额**（A-8/SC-005）：worker 接收后拖给自己的 agent → Run
  completed 且 owner_id == 需求创建者；owner 收到 run_completed 通知。
- **F 边界**（A-5/A-6/A-9/AC-4）：陌生人 404；running 后分派 409；
  未接收拖 agent 409；重复 accept 409。
- **G 并发**（SC-004）：两个并发 accept → 恰一个 202 一个 409；历史一条 accept。
- **H 回归**：不在本脚本——由 s1–s4 与 strategy 脚本覆盖（FR-014 零行为变更）。

依赖：真实 LLM 端点或 mock 网关任一（场景 E 需要一次真实执行）。
**模型名必须与所打栈的 LLM 端点匹配**——`--model` 默认取当前部署的
`Deepseek-V4.1-Flash`（`deploy/.env` 的 `LLM_MODEL`）；用 mock 网关的栈传
`--model gpt-4o-mini`。写错模型名的症状是 Run 秒失败
`No available channel for model …`（006 换端点时踩过同款坑）。
注册走邀请码（系统无开放注册）。

用法：
    python deploy/verify/s8_request_dispatch.py --base http://localhost:8080 --invite <INVITE>
    python deploy/verify/s8_request_dispatch.py --base http://localhost:8080 --invite <INVITE> --model gpt-4o-mini
"""

import argparse
import asyncio
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
        cost = f"（{self.elapsed_s:.1f}s）" if self.elapsed_s else ""
        return f"{mark} {self.name} — {self.detail}{cost}"


class DispatchVerifier:
    def __init__(self, base: str, invite: str, model: str = "Deepseek-V4.1-Flash") -> None:
        self.base = base.rstrip("/")
        self.invite = invite
        self.model = model
        self.results: list[Result] = []
        self._http = httpx.AsyncClient(timeout=60.0)
        # 三个身份的 token
        self._tokens: dict[str, str] = {}
        self._ids: dict[str, str] = {}

    # ---------- 基础 ----------

    async def setup(self) -> None:
        for role_name in ("owner", "worker", "other"):
            email = f"disp-{role_name}-{uuid.uuid4().hex[:8]}@test.com"
            resp = await self._http.post(
                f"{self.base}/api/v1/auth/register",
                json={
                    "email": email,
                    "password": "TestPass123!",
                    "invite_code": self.invite,
                },
            )
            assert resp.status_code == 201, f"注册失败: {resp.status_code} {resp.text[:300]}"
            body = resp.json()
            self._tokens[role_name] = body["tokens"]["access"]
            self._ids[role_name] = body["user"]["id"]

    def _headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._tokens[who]}"}

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    async def _create_request(self, who: str, title: str) -> dict:
        resp = await self._http.post(
            f"{self.base}/api/v1/requests",
            headers=self._headers(who),
            json={
                "title": title,
                "description": "请给出一句话结论",
                "expected_output": "一句话结论",
                "priority": "normal",
            },
        )
        assert resp.status_code == 201, f"建需求失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _create_agent(self, who: str) -> dict:
        resp = await self._http.post(
            f"{self.base}/api/v1/agents",
            headers=self._headers(who),
            json={
                "name": f"分派验收 {uuid.uuid4().hex[:6]}",
                "system_prompt": "你是验收助手，请简短回答。",
                "model": self.model,
                "max_steps": 4,
                "cost_limit_cents": 10000,
                "tools": [],
            },
        )
        assert resp.status_code == 201, f"建 agent 失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _unread(self, who: str) -> int:
        resp = await self._http.get(
            f"{self.base}/api/v1/notifications/unread-count",
            headers=self._headers(who),
        )
        assert resp.status_code == 200
        return resp.json()["unread"]

    async def _wait_for_terminal(self, run_id: str, who: str, timeout_s: float = 180.0) -> dict:
        deadline = time.monotonic() + timeout_s
        last: dict = {}
        while time.monotonic() < deadline:
            resp = await self._http.get(
                f"{self.base}/api/v1/runs/{run_id}", headers=self._headers(who)
            )
            assert resp.status_code == 200
            last = resp.json()
            if last["status"] in {"completed", "failed", "cancelled", "timeout"}:
                return last
            await asyncio.sleep(2)
        raise AssertionError(f"Run {run_id} 未在 {timeout_s}s 内终态，最后状态 {last.get('status')}")

    # ---------- 场景 ----------

    async def scenario_a_dispatch_closure(self) -> None:
        """A 分派闭环：202 pending + 对方列表可见 + 未读 +1 + 受理 <100ms。"""
        started = time.monotonic()
        req = await self._create_request("owner", f"分派闭环 {uuid.uuid4().hex[:6]}")
        before_unread = await self._unread("worker")

        t0 = time.monotonic()
        resp = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("owner"),
            json={
                "assignee_type": "user",
                "assignee_id": self._ids["worker"],
                "note": "请这周内给初稿",
            },
        )
        accept_ms = (time.monotonic() - t0) * 1000

        ok = resp.status_code == 202 and resp.json().get("status") == "pending"
        detail = f"assign(user) → {resp.status_code} {resp.json().get('status', '')}，受理 {accept_ms:.0f}ms"

        if ok:
            # worker 的 relevant 列表应含该需求（默认 scope）
            listing = await self._http.get(
                f"{self.base}/api/v1/requests", headers=self._headers("worker")
            )
            ids = {item["id"] for item in listing.json()["items"]}
            ok = req["id"] in ids
            detail += f"；worker 列表含该需求={req['id'] in ids}"

            after_unread = await self._unread("worker")
            ok = ok and after_unread == before_unread + 1
            detail += f"；未读 {before_unread} → {after_unread}"

            # SC-001：受理 P95 < 100ms（单次采样，脚本级 sanity）
            ok = ok and accept_ms < 100
            detail += f"；受理 <100ms={accept_ms < 100}"

        self._record("A 分派闭环", ok, detail, started)
        self._req_a = req["id"]

    async def scenario_b_accept(self) -> None:
        """B 接收：worker accept → accepted + owner 收到通知；other accept → 404。"""
        started = time.monotonic()
        resp = await self._http.post(
            f"{self.base}/api/v1/requests/{self._req_a}/accept",
            headers=self._headers("worker"),
        )
        ok = resp.status_code == 202 and resp.json()["status"] == "accepted"
        detail = f"accept → {resp.status_code} {resp.json().get('status', '')}"

        if ok:
            # owner（分派人）应收到 request_accepted 通知
            notifs = await self._http.get(
                f"{self.base}/api/v1/notifications?unread_only=true",
                headers=self._headers("owner"),
            )
            types = [n["type"] for n in notifs.json()["items"]]
            ok = "request_accepted" in types
            detail += f"；owner 未读含 request_accepted={'request_accepted' in types}"

            # other（陌生人）accept → 404
            stranger = await self._http.post(
                f"{self.base}/api/v1/requests/{self._req_a}/accept",
                headers=self._headers("other"),
            )
            ok = ok and stranger.status_code == 404
            detail += f"；陌生人 accept → {stranger.status_code}"

        self._record("B 接收", ok, detail, started)

    async def scenario_c_reject(self) -> None:
        """C 退回：附原因 → created + 通知含原因；空原因 → 422。"""
        started = time.monotonic()
        # 先重新分派一条给 worker（上一条已被 accept）
        req = await self._create_request("owner", f"退回 {uuid.uuid4().hex[:6]}")
        await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["worker"]},
        )

        bad = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/reject",
            headers=self._headers("worker"),
            json={"reason": "   "},
        )
        ok = bad.status_code == 422
        detail = f"空原因 → {bad.status_code}"

        resp = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/reject",
            headers=self._headers("worker"),
            json={"reason": "这周排期已满，建议下周启动"},
        )
        ok = ok and resp.status_code == 202 and resp.json()["status"] == "created"
        detail += f"；附原因 reject → {resp.status_code} {resp.json().get('status', '')}"

        if ok:
            notifs = await self._http.get(
                f"{self.base}/api/v1/notifications?unread_only=true",
                headers=self._headers("owner"),
            )
            rejected = [n for n in notifs.json()["items"] if n["type"] == "request_rejected"]
            ok = any(n["payload"].get("reason") == "这周排期已满，建议下周启动" for n in rejected)
            detail += f"；owner 收到含原因通知={ok}"

        self._record("C 退回", ok, detail, started)

    async def scenario_d_reassign(self) -> None:
        """D 转交：pending 再拖给第三人 → dispatch+reassign + 旧接收人收通知。"""
        started = time.monotonic()
        req = await self._create_request("owner", f"转交 {uuid.uuid4().hex[:6]}")
        await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["worker"]},
        )
        before_unread_other = await self._unread("other")

        resp = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["other"]},
        )
        ok = resp.status_code == 202
        detail = f"二次 assign(user) → {resp.status_code}"

        if ok:
            detail_resp = await self._http.get(
                f"{self.base}/api/v1/requests/{req['id']}", headers=self._headers("owner")
            )
            actions = [h["action"] for h in detail_resp.json()["dispatch_history"]]
            ok = actions == ["dispatch", "reassign"]
            detail += f"；历史 {actions}"

            after_unread_other = await self._unread("other")
            ok = ok and after_unread_other == before_unread_other + 1
            detail += f"；other 未读 +1={after_unread_other == before_unread_other + 1}"

        self._record("D 转交", ok, detail, started)

    async def scenario_e_execute_and_quota(self) -> None:
        """E 执行与配额：接收后拖给自己的 agent → completed + owner_id=创建者 + 通知。"""
        started = time.monotonic()
        req = await self._create_request("owner", f"执行 {uuid.uuid4().hex[:6]}")
        await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["worker"]},
        )
        await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/accept",
            headers=self._headers("worker"),
        )
        agent = await self._create_agent("worker")

        resp = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("worker"),
            json={"assignee_type": "agent", "assignee_id": agent["id"]},
        )
        ok = resp.status_code == 202 and "run_id" in resp.json()
        detail = f"接收人拖给自己的 agent → {resp.status_code}"

        if ok:
            run = await self._wait_for_terminal(resp.json()["run_id"], "worker")
            ok = run["status"] == "completed"
            detail += f"；Run {run['status']}"

            # SC-005：Run 归属 = 需求创建者（成本恒记 owner）
            run_owner = run.get("owner_id")
            ok = ok and run_owner == self._ids["owner"]
            detail += f"；run.owner_id == 创建者={run_owner == self._ids['owner']}"

            # FR-010：创建者收到 run_completed 通知
            notifs = await self._http.get(
                f"{self.base}/api/v1/notifications?unread_only=true",
                headers=self._headers("owner"),
            )
            types = [n["type"] for n in notifs.json()["items"]]
            ok = ok and "run_completed" in types
            detail += f"；owner 收到 run_completed={'run_completed' in types}"

        self._record("E 执行与配额", ok, detail, started)

    async def scenario_f_boundaries(self) -> None:
        """F 边界：陌生人 404 / running 后分派 409 / 未接收拖 agent 409 / 重复 accept 409。"""
        started = time.monotonic()
        req = await self._create_request("owner", f"边界 {uuid.uuid4().hex[:6]}")
        checks: list[str] = []

        # 未分派时陌生人操作 → 404
        stranger = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/accept",
            headers=self._headers("other"),
        )
        checks.append(f"陌生人 accept={stranger.status_code}")

        # 分派后未接收拖 agent → 409
        await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["worker"]},
        )
        agent = await self._create_agent("worker")
        early = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("worker"),
            json={"assignee_type": "agent", "assignee_id": agent["id"]},
        )
        checks.append(f"未接收拖 agent={early.status_code}")

        # 重复 accept → 409
        await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/accept",
            headers=self._headers("worker"),
        )
        dup = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/accept",
            headers=self._headers("worker"),
        )
        checks.append(f"重复 accept={dup.status_code}")

        # 已入队执行后分派 → 409
        run_resp = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("worker"),
            json={"assignee_type": "agent", "assignee_id": agent["id"]},
        )
        checks.append(f"accepted 拖 agent={run_resp.status_code}")
        if run_resp.status_code == 202:
            await self._wait_for_terminal(run_resp.json()["run_id"], "worker")
        after = await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["other"]},
        )
        checks.append(f"终态后再分派={after.status_code}")

        ok = (
            stranger.status_code == 404
            and early.status_code == 409
            and dup.status_code == 409
            and run_resp.status_code == 202
            and after.status_code == 409
        )
        self._record("F 边界", ok, "，".join(checks), started)

    async def scenario_g_concurrent_accept(self) -> None:
        """G 并发：两个并发 accept → 恰一个 202 一个 409；历史一条 accept。"""
        started = time.monotonic()
        req = await self._create_request("owner", f"并发 {uuid.uuid4().hex[:6]}")
        await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["worker"]},
        )

        async def _accept():
            return await self._http.post(
                f"{self.base}/api/v1/requests/{req['id']}/accept",
                headers=self._headers("worker"),
            )

        r1, r2 = await asyncio.gather(_accept(), _accept())
        codes = sorted([r1.status_code, r2.status_code])
        ok = codes == [202, 409]
        detail = f"并发 accept → {codes}"

        if ok:
            detail_resp = await self._http.get(
                f"{self.base}/api/v1/requests/{req['id']}", headers=self._headers("owner")
            )
            accepts = [
                h for h in detail_resp.json()["dispatch_history"] if h["action"] == "accept"
            ]
            ok = len(accepts) == 1
            detail += f"；历史 accept {len(accepts)} 条"

        self._record("G 并发接收", ok, detail, started)

    async def run(self) -> int:
        await self.setup()
        await self.scenario_a_dispatch_closure()
        await self.scenario_b_accept()
        await self.scenario_c_reject()
        await self.scenario_d_reassign()
        await self.scenario_e_execute_and_quota()
        await self.scenario_f_boundaries()
        await self.scenario_g_concurrent_accept()
        await self._http.aclose()

        print()
        for r in self.results:
            print(r.line())
        failed = [r for r in self.results if not r.ok]
        print(f"\n合计 {len(self.results)} 场景：{len(self.results) - len(failed)} 通过 / {len(failed)} 失败")
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="007 需求分派真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True, help="邀请码（系统无开放注册）")
    parser.add_argument(
        "--model",
        default="Deepseek-V4.1-Flash",
        help="验收 Agent 使用的模型（必须与所打栈的 LLM 端点匹配）",
    )
    args = parser.parse_args()
    verifier = DispatchVerifier(args.base, args.invite, model=args.model)
    raise SystemExit(asyncio.run(verifier.run()))


if __name__ == "__main__":
    main()
