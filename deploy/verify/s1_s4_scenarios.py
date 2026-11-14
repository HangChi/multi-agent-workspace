"""S1~S4 全场景验证脚本（quickstart.md S1~S4，跑在真实 docker compose 栈上）。

与 integration 测试（TestClient 进程内）互补：这里打真实 api 容器 + postgres + redis + minio，
验证部署形态下的行为一致。

覆盖：
- S1 注册登录与权限（FR-001~003 / SC-001, SC-005）
- S2 Agent 与组管理（FR-004~007）
- S3 需求与看板 + 附件预签名 PUT/GET（FR-008~009）
- S4 拖拽分配与任务入队（FR-010~013 / SC-002）

用法：
  python s1_s4_scenarios.py --base http://localhost:8080
前置：invite_codes 中存在 VERIFY-MEMBER-A / VERIFY-MEMBER-B / TEST-VIEWER-CODE-456。
"""

import argparse
import asyncio
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

PASSWORD = "Verify123!"

INVITE_MEMBER_A = "VERIFY-MEMBER-A"
INVITE_MEMBER_B = "VERIFY-MEMBER-B"
INVITE_VIEWER = "TEST-VIEWER-CODE-456"


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""
    elapsed_s: float = 0.0

    def line(self) -> str:
        mark = "✅" if self.ok else "❌"
        tail = f"（{self.elapsed_s:.2f}s）" if self.elapsed_s else ""
        return f"{mark} {self.name} — {self.detail}{tail}"


def _minio_request(method: str, url: str, content: bytes = b"") -> tuple[int, bytes]:
    """在 api 容器内发请求到 MinIO。

    预签名 URL 的签名含 Host（容器名 minio:9000），在宿主机替换 host 会导致
    SignatureDoesNotMatch（403）。因此上传/下载都在容器网络内执行——这也更贴近
    生产形态（api 与 minio 同网络）。
    """
    code = (
        "import sys,urllib.request\n"
        "url=sys.argv[1]; method=sys.argv[2]\n"
        "data=sys.stdin.buffer.read() if method=='PUT' else None\n"
        "req=urllib.request.Request(url,data=data,method=method)\n"
        "try:\n"
        "    r=urllib.request.urlopen(req)\n"
        "    sys.stdout.buffer.write(str(r.status).encode()+b'\\n'+r.read())\n"
        "except Exception as e:\n"
        "    sys.stdout.buffer.write(('ERR '+str(e)).encode())\n"
    )
    out = subprocess.run(
        ["docker", "exec", "-i", "aw-api", "python", "-c", code, url, method],
        input=content,
        capture_output=True,
        check=True,
    )
    head, _, rest = out.stdout.partition(b"\n")
    try:
        return int(head), rest
    except ValueError:
        return 0, head


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "aw-postgres", "psql", "-U", "aw", "-d", "agent_workbench", "-tAc", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


class Verifier:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.http = httpx.AsyncClient(timeout=30.0)
        self.results: list[Result] = []
        self.tok: dict[str, str] = {}
        self.user: dict[str, dict[str, Any]] = {}

    # ---------- 基础 ----------

    async def _auth(self, key: str, headers: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.tok[key]}"}
        if headers:
            h.update(headers)
        return h

    async def register(self, key: str, email: str, invite: str) -> httpx.Response:
        """注册；邮箱已存在（重复跑）则登录，保证脚本幂等。"""
        resp = await self.http.post(
            f"{self.base}/api/v1/auth/register",
            json={"email": email, "password": PASSWORD, "invite_code": invite},
        )
        if resp.status_code == 409:
            resp = await self.http.post(
                f"{self.base}/api/v1/auth/login", json={"email": email, "password": PASSWORD}
            )
        assert resp.status_code in (200, 201), f"{key} 注册/登录失败 {resp.status_code} {resp.text[:200]}"
        body = resp.json()
        self.tok[key] = body["tokens"]["access"]
        self.tok[f"{key}_refresh"] = body["tokens"]["refresh"]
        self.user[key] = body["user"]
        return resp

    async def setup(self) -> None:
        suffix = uuid.uuid4().hex[:6]
        await self.register("a", f"verify-a-{suffix}@test.com", INVITE_MEMBER_A)
        await self.register("b", f"verify-b-{suffix}@test.com", INVITE_MEMBER_B)
        await self.register("viewer", f"verify-v-{suffix}@test.com", INVITE_VIEWER)

    # ---------- S1 ----------

    async def s1_auth(self) -> None:
        start = time.monotonic()
        register_resp = await self.http.post(
            f"{self.base}/api/v1/auth/register",
            json={
                "email": f"verify-s1-{uuid.uuid4().hex[:6]}@test.com",
                "password": PASSWORD,
                "invite_code": INVITE_MEMBER_A,
            },
        )
        auto_login = register_resp.status_code == 201 and bool(
            register_resp.json().get("tokens", {}).get("access")
        )
        new_tok = register_resp.json()["tokens"]["access"]
        me = await self.http.get(f"{self.base}/api/v1/auth/me", headers={"Authorization": f"Bearer {new_tok}"})
        elapsed = time.monotonic() - start
        self.results.append(
            Result(
                "SC-001 注册即自动登录 <2min",
                auto_login and me.status_code == 200 and me.json()["role"] == "member",
                f"register={register_resp.status_code} 自动登录={auto_login} me.role={me.json().get('role')}",
                elapsed,
            )
        )

        login = await self.http.post(
            f"{self.base}/api/v1/auth/login",
            json={"email": self.user["a"]["email"], "password": PASSWORD},
        )
        self.results.append(
            Result("S1-2 凭证登录", login.status_code == 200 and "access" in login.json()["tokens"], f"{login.status_code}")
        )

        refresh = await self.http.post(
            f"{self.base}/api/v1/auth/refresh", json={"refresh_token": self.tok["a_refresh"]}
        )
        rbody = refresh.json() if refresh.status_code == 200 else {}
        self.results.append(
            Result(
                "S1-3 refresh 换发",
                refresh.status_code == 200 and bool(rbody.get("tokens", {}).get("access")),
                f"{refresh.status_code} tokens={'yes' if rbody.get('tokens', {}).get('access') else 'no'}",
            )
        )

        wrong = await self.http.post(
            f"{self.base}/api/v1/auth/login",
            json={"email": self.user["a"]["email"], "password": "WrongPass1!"},
        )
        self.results.append(Result("S1-4 密码错误 403", wrong.status_code == 403, f"{wrong.status_code}"))

        bad_invite = await self.http.post(
            f"{self.base}/api/v1/auth/register",
            json={"email": f"bad-{uuid.uuid4().hex[:6]}@test.com", "password": PASSWORD, "invite_code": "NO-SUCH-CODE"},
        )
        self.results.append(Result("S1-5 无效邀请码 400", bad_invite.status_code == 400, f"{bad_invite.status_code}"))

        unauth = await self.http.get(f"{self.base}/api/v1/requests")
        self.results.append(Result("SC-005 未认证 401", unauth.status_code == 401, f"{unauth.status_code}"))

        # SC-005：viewer 创建 Agent 必须 403
        viewer_agent = await self.http.post(
            f"{self.base}/api/v1/agents",
            headers=await self._auth("viewer"),
            json={"name": "viewer-forbidden", "system_prompt": "x", "model": "gpt-4o-mini",
                  "max_steps": 3, "cost_limit_cents": 100},
        )
        self.results.append(
            Result("SC-005 viewer 创建 Agent 403", viewer_agent.status_code == 403, f"{viewer_agent.status_code}")
        )

        # SC-005：member 访问审计后台必须 403（真实路径 /api/v1/audit-logs，见 admin.py prefix）
        member_admin = await self.http.get(
            f"{self.base}/api/v1/audit-logs", headers=await self._auth("a")
        )
        self.results.append(
            Result("SC-005 member 访问审计 403", member_admin.status_code == 403, f"{member_admin.status_code}")
        )

    # ---------- S2 ----------

    async def _create_agent(self, key: str, *, name: str | None = None, cost_limit: int = 10000) -> dict[str, Any]:
        resp = await self.http.post(
            f"{self.base}/api/v1/agents",
            headers=await self._auth(key),
            json={
                "name": name or f"verify-agent-{uuid.uuid4().hex[:6]}",
                "system_prompt": "你是任务执行助手",
                "model": "gpt-4o-mini",
                "max_steps": 5,
                "cost_limit_cents": cost_limit,
                "tools": ["web_search"],
            },
        )
        assert resp.status_code == 201, f"创建 agent 失败 {resp.status_code} {resp.text[:200]}"
        return resp.json()

    async def s2_agents_groups(self) -> None:
        agent = await self._create_agent("a")
        owner_ok = agent.get("owner_id") == self.user["a"]["id"]
        self.results.append(
            Result("S2-1 创建 Agent 201", owner_ok, f"id={agent['id'][:8]} cost_limit={agent['cost_limit_cents']}")
        )

        patch = await self.http.patch(
            f"{self.base}/api/v1/agents/{agent['id']}",
            headers=await self._auth("a"),
            json={"system_prompt": "你是资深调研助手（已编辑）", "cost_limit_cents": 777},
        )
        ok = patch.status_code == 200 and patch.json()["cost_limit_cents"] == 777
        self.results.append(Result("S2-2 编辑 Agent 生效", ok, f"{patch.status_code} 新上限={patch.json().get('cost_limit_cents')}"))

        forbidden = await self.http.patch(
            f"{self.base}/api/v1/agents/{agent['id']}",
            headers=await self._auth("b"),
            json={"system_prompt": "hijack"},
        )
        self.results.append(
            Result("S2-4 非 owner 编辑 403", forbidden.status_code == 403, f"{forbidden.status_code}")
        )

        agent2 = await self._create_agent("a")
        group = await self.http.post(
            f"{self.base}/api/v1/groups",
            headers=await self._auth("a"),
            json={"name": f"verify-group-{uuid.uuid4().hex[:6]}", "routing_strategy": "fan_out"},
        )
        assert group.status_code == 201, f"创建组失败 {group.status_code} {group.text[:200]}"
        gid = group.json()["id"]

        for pos, ag in enumerate([agent, agent2], start=1):
            added = await self.http.post(
                f"{self.base}/api/v1/groups/{gid}/members",
                headers=await self._auth("a"),
                json={"agent_id": ag["id"], "position": pos},
            )
            assert added.status_code == 201, f"添加成员失败 {added.status_code} {added.text[:200]}"

        detail = await self.http.get(f"{self.base}/api/v1/groups/{gid}", headers=await self._auth("a"))
        body = detail.json() if detail.status_code == 200 else {}
        strategy = body.get("routing_strategy")
        members = body.get("members") or body.get("items") or []
        ok = strategy == "fan_out" and len(members) == 2
        self.results.append(
            Result("S2-3 组创建+成员+策略持久化", ok, f"{detail.status_code} strategy={strategy} members={len(members)}")
        )

        dup = await self.http.post(
            f"{self.base}/api/v1/groups/{gid}/members",
            headers=await self._auth("a"),
            json={"agent_id": agent["id"], "position": 3},
        )
        self.results.append(Result("S2-5 重复成员 409", dup.status_code == 409, f"{dup.status_code}"))

        del_ref = await self.http.delete(
            f"{self.base}/api/v1/agents/{agent['id']}", headers=await self._auth("a")
        )
        archived = await self.http.post(
            f"{self.base}/api/v1/agents/{agent2['id']}/archive", headers=await self._auth("a")
        )
        self.results.append(
            Result(
                "S2-6 组引用仅可归档（DELETE 409 / archive 200）",
                del_ref.status_code == 409 and archived.status_code == 200,
                f"delete={del_ref.status_code} archive={archived.status_code}",
            )
        )

        # 归档 Agent 不出现在可分配（active）列表
        listed = await self.http.get(
            f"{self.base}/api/v1/agents?status=active&page_size=100", headers=await self._auth("a")
        )
        ids = [i["id"] for i in listed.json().get("items", [])]
        self.results.append(
            Result("S2-7 归档 Agent 不进 active 列表", agent2["id"] not in ids, f"active_total={len(ids)}")
        )

        self.ctx = {"agent": agent, "agent2": agent2, "group": gid}

    # ---------- S3 ----------

    async def _create_request(self, key: str, title: str, desc: str, priority: str = "normal") -> dict[str, Any]:
        resp = await self.http.post(
            f"{self.base}/api/v1/requests",
            headers=await self._auth(key),
            json={"title": title, "description": desc, "expected_output": "三行要点", "priority": priority},
        )
        assert resp.status_code == 201, f"创建需求失败 {resp.status_code} {resp.text[:200]}"
        return resp.json()

    async def s3_requests_board(self) -> None:
        marker = f"KWMARK{uuid.uuid4().hex[:6].upper()}"
        req = await self._create_request("a", f"S3 需求 {marker}", "整理一份简明调研报告", priority="high")
        self.results.append(
            Result("S3-1 创建需求 201 status=created", req["status"] == "created", f"priority={req['priority']}")
        )

        # 附件：预签名 PUT → 真实上传 → 下载 302
        content = b"verify attachment payload\n"
        up = await self.http.post(
            f"{self.base}/api/v1/requests/{req['id']}/attachments",
            headers=await self._auth("a"),
            params={"filename": "note.txt", "size": len(content), "mime_type": "text/plain"},
        )
        ok_meta = up.status_code == 201 and bool(up.json()["attachments"][0].get("presigned_put_url"))
        put_url = ""
        if ok_meta:
            put_url = up.json()["attachments"][0]["presigned_put_url"]
        put_status = _minio_request("PUT", put_url, content)[0] if put_url else 0
        key = up.json()["attachments"][0]["key"] if ok_meta else ""
        dl = await self.http.get(
            f"{self.base}/api/v1/requests/{req['id']}/attachments/{quote(key, safe='/')}",
            headers=await self._auth("a"),
        )
        fetched = b""
        if dl.status_code == 302:
            fetched = _minio_request("GET", dl.headers["location"])[1]
        self.results.append(
            Result(
                "S3-2 附件 预签名PUT→下载内容一致",
                ok_meta and put_status == 200 and dl.status_code == 302 and fetched == content,
                f"meta={up.status_code} put={put_status} get={dl.status_code} bytes={len(fetched)}",
            )
        )

        detail = await self.http.get(f"{self.base}/api/v1/requests/{req['id']}", headers=await self._auth("a"))
        self.results.append(
            Result(
                "S3-3 详情回填附件元数据",
                detail.status_code == 200 and len(detail.json().get("attachments") or []) >= 1,
                f"attachments={len(detail.json().get('attachments') or [])}",
            )
        )

        board = await self.http.get(
            f"{self.base}/api/v1/requests?status=created&page_size=100", headers=await self._auth("a")
        )
        items = board.json().get("items", [])
        self.results.append(
            Result(
                "S3-4 看板按状态分组",
                board.status_code == 200 and all(i["status"] == "created" for i in items),
                f"created={len(items)} total={board.json().get('total')}",
            )
        )

        search = await self.http.get(
            f"{self.base}/api/v1/requests?q={marker}", headers=await self._auth("a")
        )
        found = search.json().get("items", [])
        self.results.append(
            Result(
                "S3-5 关键词搜索命中唯一",
                len(found) == 1 and found[0]["id"] == req["id"],
                f"hits={len(found)}",
            )
        )

        big = await self.http.post(
            f"{self.base}/api/v1/requests/{req['id']}/attachments",
            headers=await self._auth("a"),
            params={"filename": "big.bin", "size": 21 * 1024 * 1024, "mime_type": "application/octet-stream"},
        )
        exe = await self.http.post(
            f"{self.base}/api/v1/requests/{req['id']}/attachments",
            headers=await self._auth("a"),
            params={"filename": "run.exe", "size": 1024, "mime_type": "application/x-msdownload"},
        )
        self.results.append(
            Result(
                "S3-6 超 20MB / 类型白名单拒绝 400",
                big.status_code == 400 and exe.status_code == 400,
                f"size={big.status_code} mime={exe.status_code}",
            )
        )

        other = await self.http.get(
            f"{self.base}/api/v1/requests/{req['id']}", headers=await self._auth("b")
        )
        self.results.append(
            Result("S3-7 非 owner 读取 403", other.status_code == 403, f"{other.status_code}")
        )

        self.ctx["request"] = req

    # ---------- S4 ----------

    async def _assign(self, key: str, request_id: str, assignee_type: str, assignee_id: str,
                      strategy: str | None = None) -> tuple[httpx.Response, float]:
        body: dict[str, Any] = {"assignee_type": assignee_type, "assignee_id": assignee_id}
        if strategy:
            body["strategy"] = strategy
        t0 = time.monotonic()
        resp = await self.http.post(
            f"{self.base}/api/v1/requests/{request_id}/assign",
            headers=await self._auth(key),
            json=body,
        )
        return resp, time.monotonic() - t0

    async def s4_assign(self) -> None:
        agent = self.ctx["agent"]
        req = self.ctx["request"]
        resp, dt = await self._assign("a", req["id"], "agent", agent["id"])
        body = resp.json() if resp.status_code == 202 else {}
        detail = await self.http.get(
            f"{self.base}/api/v1/requests/{req['id']}", headers=await self._auth("a")
        )
        self.results.append(
            Result(
                "S4-1 拖到 Agent 202 入队 + 卡片转执行中",
                resp.status_code == 202 and body.get("status") == "queued"
                and detail.json()["status"] == "running",
                f"{resp.status_code} run={body.get('run_id', '')[:8]} request={detail.json()['status']}",
            )
        )
        self.results.append(
            Result("SC-002 受理延迟 <3s", dt < 3.0, f"{dt * 1000:.0f}ms")
        )

        again, _ = await self._assign("a", req["id"], "agent", agent["id"])
        self.results.append(Result("S4-3 重复分配 409", again.status_code == 409, f"{again.status_code}"))

        foreign, _ = await self._assign("b", req["id"], "agent", agent["id"])
        self.results.append(
            Result("S4-4 非 owner 分配 403/404", foreign.status_code in (403, 404), f"{foreign.status_code}")
        )

        # fan_out：新建需求 + 组（组里 agent2 已归档 → 仅 1 个 active 成员，故重建含 2 active 的组）
        a3 = await self._create_agent("a")
        grp = await self.http.post(
            f"{self.base}/api/v1/groups",
            headers=await self._auth("a"),
            json={"name": f"verify-fanout-{uuid.uuid4().hex[:6]}", "routing_strategy": "fan_out"},
        )
        gid = grp.json()["id"]
        for pos, ag in enumerate([agent, a3], start=1):
            added = await self.http.post(
                f"{self.base}/api/v1/groups/{gid}/members",
                headers=await self._auth("a"),
                json={"agent_id": ag["id"], "position": pos},
            )
            assert added.status_code == 201, added.text[:200]

        req2 = await self._create_request("a", f"S4 fanout {uuid.uuid4().hex[:5]}", "简述要点")
        group_resp, group_dt = await self._assign("a", req2["id"], "group", gid)
        parent_id = group_resp.json().get("run_id") if group_resp.status_code == 202 else ""
        children = 0
        if parent_id:
            await asyncio.sleep(0.2)
            rows = psql(f"select count(*) from runs where parent_run_id = '{parent_id}';")
            children = int(rows or 0)
        self.results.append(
            Result(
                "S4-2 拖到组 fan_out 生成子 Run",
                group_resp.status_code == 202 and children == 2,
                f"{group_resp.status_code} parent={parent_id[:8]} children={children}",
                group_dt,
            )
        )

        # 归档目标 → 400
        req3 = await self._create_request("a", f"S4 archived {uuid.uuid4().hex[:5]}", "简述要点")
        archived_assign, _ = await self._assign("a", req3["id"], "agent", self.ctx["agent2"]["id"])
        self.results.append(
            Result("S4-5 拖到归档 Agent 400", archived_assign.status_code == 400, f"{archived_assign.status_code}")
        )

        nonexist, _ = await self._assign("a", req3["id"], "agent", str(uuid.uuid4()))
        self.results.append(
            Result("S4-6 目标不存在 404", nonexist.status_code == 404, f"{nonexist.status_code}")
        )

    async def run_all(self) -> None:
        await self.setup()
        await self.s1_auth()
        await self.s2_agents_groups()
        await self.s3_requests_board()
        await self.s4_assign()

    def summary(self) -> tuple[bool, str]:
        return all(r.ok for r in self.results), "\n".join(r.line() for r in self.results)


async def main() -> int:
    ap = argparse.ArgumentParser(description="S1~S4 真实栈验证")
    ap.add_argument("--base", default="http://localhost:8080")
    args = ap.parse_args()

    v = Verifier(args.base)
    try:
        await v.run_all()
    except Exception as exc:  # noqa: BLE001 - 汇总输出
        v.results.append(Result("脚本异常", False, f"{type(exc).__name__}: {str(exc)[:200]}"))
    ok, lines = v.summary()
    print("\n===== S1~S4 验证结果 =====")
    print(lines)
    print(f"\n总计：{'✅ 全部通过' if ok else '❌ 存在失败'}（{len(v.results)} 项）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
