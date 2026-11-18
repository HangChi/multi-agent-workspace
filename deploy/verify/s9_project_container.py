"""项目容器的真实栈验收（008 / T026）。

五个场景对应 spec 的成功标准，全部走 HTTP 打真实容器栈：

- **A 建项目并归类**（US1/P-1）：建 2 项目；3 条需求分别归属；详情与
  列表的 project_id/name 正确；同名 409。
- **B 管理动作**（US2/P-5~P-7）：改名后旧需求 name 同步；归档后新建 400
  但既有链路不变；删除后需求归属两列清空且需求/Run 计数不变。
- **C 筛选与深链**（US3/FR-006）：`?project_id=` / `?project=none` 各自
  生效且与 scope 正交；列表计数与逐条 project_name 一致。
- **D 权限边界**（P-3/P-4/FR-005）：他人项目 404；viewer 403；经手人改
  project_id 403 但改标题 202。
- **E 回归**（SC-003/SC-006）：s8 原样重跑仍 7/7（007 行为零变更）；
  s1–s4 29/29。

008 本身不调 LLM，但场景 A/B 会建需求（不入队），E 需要真实执行。
注册走邀请码（系统无开放注册）。

用法：
    python deploy/verify/s9_project_container.py --base http://localhost:8080 --invite <INVITE>
"""

import argparse
import asyncio
import subprocess
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
        cost = f"（{self.elapsed_s:.1f}s）" if self.elapsed_s else ""
        return f"{mark} {self.name} — {self.detail}{cost}"


class ProjectVerifier:
    def __init__(self, base: str, invite: str, viewer_invite: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.invite = invite
        # viewer 邀请码（可选）：不提供则跳过 viewer 场景的实测，
        # 由 008 集成测试（test_projects.py::test_viewer_forbidden）钉住。
        self.viewer_invite = viewer_invite
        self.results: list[Result] = []
        self._http = httpx.AsyncClient(timeout=60.0)
        self._tokens: dict[str, str] = {}
        self._ids: dict[str, str] = {}
        self._projects: dict[str, str] = {}

    # ---------- 基础 ----------

    async def setup(self) -> None:
        for who in ("owner", "worker", "other"):
            email = f"proj-{who}-{uuid.uuid4().hex[:8]}@test.com"
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
            self._tokens[who] = body["tokens"]["access"]
            self._ids[who] = body["user"]["id"]

    def _headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._tokens[who]}"}

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    async def _create_project(self, who: str, name: str, description: str | None = None) -> dict:
        resp = await self._http.post(
            f"{self.base}/api/v1/projects",
            headers=self._headers(who),
            json={"name": name, "description": description},
        )
        assert resp.status_code == 201, f"建项目失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    async def _create_request(self, who: str, title: str, project_id: str | None = None) -> dict:
        body: dict = {"title": title}
        if project_id:
            body["project_id"] = project_id
        resp = await self._http.post(
            f"{self.base}/api/v1/requests",
            headers=self._headers(who),
            json=body,
        )
        assert resp.status_code == 201, f"建需求失败: {resp.status_code} {resp.text[:300]}"
        return resp.json()

    # ---------- 场景 ----------

    async def scenario_a_create_and_classify(self) -> None:
        """A：建项目 + 归类 + 详情/列表字段 + 同名 409。"""
        started = time.monotonic()
        checks: list[str] = []

        p1 = await self._create_project("owner", f"官网改版-{uuid.uuid4().hex[:4]}", "Q4 重构")
        p2 = await self._create_project("owner", f"增长实验-{uuid.uuid4().hex[:4]}")
        self._projects["a"] = p1["id"]
        self._projects["b"] = p2["id"]
        checks.append(f"建 2 项目 201×2")

        r1 = await self._create_request("owner", "A-需求1", p1["id"])
        r2 = await self._create_request("owner", "A-需求2", p1["id"])
        await self._create_request("owner", "A-需求3", p2["id"])

        detail = await self._http.get(
            f"{self.base}/api/v1/requests/{r1['id']}", headers=self._headers("owner")
        )
        body = detail.json()
        ok_detail = (
            body["project_id"] == p1["id"] and body["project_name"] == p1["name"]
        )
        checks.append(f"详情字段={ok_detail}")

        # 列表同样带快照
        listing = await self._http.get(
            f"{self.base}/api/v1/requests", headers=self._headers("owner")
        )
        items = {i["id"]: i for i in listing.json()["items"]}
        ok_list = (
            items[r1["id"]]["project_name"] == p1["name"]
            and items[r2["id"]]["project_id"] == p1["id"]
        )
        checks.append(f"列表快照={ok_list}")

        dup = await self._http.post(
            f"{self.base}/api/v1/projects",
            headers=self._headers("owner"),
            json={"name": p1["name"]},
        )
        checks.append(f"同名 409={dup.status_code}")

        # 计数（FR-010）：p1 下 2 条
        projects = await self._http.get(
            f"{self.base}/api/v1/projects", headers=self._headers("owner")
        )
        counts = {p["id"]: p["request_count"] for p in projects.json()["items"]}
        checks.append(f"计数 p1={counts.get(p1['id'])}")

        ok = (
            ok_detail
            and ok_list
            and dup.status_code == 409
            and counts.get(p1["id"]) == 2
            and counts.get(p2["id"]) == 1
        )
        self._record("A 建项目并归类", ok, "，".join(checks), started)

    async def scenario_b_management(self) -> None:
        """B：改名同步 / 归档拦新建 / 删除置空且计数不变。"""
        started = time.monotonic()
        checks: list[str] = []

        project = await self._create_project("owner", f"管理-{uuid.uuid4().hex[:4]}")
        req = await self._create_request("owner", "B-需求", project["id"])

        # 改名 → 快照同步
        new_name = f"改过的名-{uuid.uuid4().hex[:4]}"
        renamed = await self._http.patch(
            f"{self.base}/api/v1/projects/{project['id']}",
            headers=self._headers("owner"),
            json={"name": new_name},
        )
        detail = await self._http.get(
            f"{self.base}/api/v1/requests/{req['id']}", headers=self._headers("owner")
        )
        ok_rename = (
            renamed.status_code == 200
            and detail.json()["project_name"] == new_name
        )
        checks.append(f"改名同步={ok_rename}")

        # 归档 → 新建 400，既有归属不变
        archived = await self._http.patch(
            f"{self.base}/api/v1/projects/{project['id']}",
            headers=self._headers("owner"),
            json={"status": "archived"},
        )
        blocked = await self._http.post(
            f"{self.base}/api/v1/requests",
            headers=self._headers("owner"),
            json={"title": "B-往归档塞", "project_id": project["id"]},
        )
        ok_archive = archived.status_code == 200 and blocked.status_code == 400
        checks.append(f"归档拦新建={ok_archive}")

        # 恢复后删除 → 需求保留、归属清空
        await self._http.patch(
            f"{self.base}/api/v1/projects/{project['id']}",
            headers=self._headers("owner"),
            json={"status": "active"},
        )
        before_list = await self._http.get(
            f"{self.base}/api/v1/requests", headers=self._headers("owner")
        )
        before_count = len(before_list.json()["items"])
        deleted = await self._http.delete(
            f"{self.base}/api/v1/projects/{project['id']}",
            headers=self._headers("owner"),
        )
        after_list = await self._http.get(
            f"{self.base}/api/v1/requests", headers=self._headers("owner")
        )
        after_items = {i["id"]: i for i in after_list.json()["items"]}
        ok_delete = (
            deleted.status_code == 204
            and len(after_items) == before_count  # 一条没删
            and after_items[req["id"]]["project_id"] is None
            and after_items[req["id"]]["project_name"] is None
        )
        checks.append(f"删除置空={ok_delete}")

        ok = ok_rename and ok_archive and ok_delete
        self._record("B 管理动作", ok, "，".join(checks), started)

    async def scenario_c_filter(self) -> None:
        """C：两维度过滤 + 与 scope 正交。"""
        started = time.monotonic()
        checks: list[str] = []

        project = await self._create_project("owner", f"筛选-{uuid.uuid4().hex[:4]}")
        in_project = await self._create_request("owner", "C-有归属", project["id"])
        loose = await self._create_request("owner", "C-无归属")

        by_project = await self._http.get(
            f"{self.base}/api/v1/requests?project_id={project['id']}",
            headers=self._headers("owner"),
        )
        ids = {i["id"] for i in by_project.json()["items"]}
        ok_project = in_project["id"] in ids and loose["id"] not in ids

        by_none = await self._http.get(
            f"{self.base}/api/v1/requests?project=none",
            headers=self._headers("owner"),
        )
        ids = {i["id"] for i in by_none.json()["items"]}
        ok_none = loose["id"] in ids and in_project["id"] not in ids

        # 与 scope 正交：mine + project 仍是「我创建的该项目需求」
        both = await self._http.get(
            f"{self.base}/api/v1/requests?scope=mine&project_id={project['id']}",
            headers=self._headers("owner"),
        )
        ids = {i["id"] for i in both.json()["items"]}
        ok_both = ids == {in_project["id"]}

        # worker 视角：派给他一条有归属需求 → relevant 列表含它
        await self._http.post(
            f"{self.base}/api/v1/requests/{in_project['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["worker"]},
        )
        worker_view = await self._http.get(
            f"{self.base}/api/v1/requests?project_id={project['id']}",
            headers=self._headers("worker"),
        )
        ok_worker = in_project["id"] in {
            i["id"] for i in worker_view.json()["items"]
        }

        # worker 的项目下拉（relevant）必须含这个别人的项目
        worker_projects = await self._http.get(
            f"{self.base}/api/v1/projects?scope=relevant",
            headers=self._headers("worker"),
        )
        ok_dropdown = project["id"] in {
            p["id"] for p in worker_projects.json()["items"]
        }

        checks.append(f"project_id={ok_project} none={ok_none} 正交={ok_both} worker可见={ok_worker} 下拉含他人项目={ok_dropdown}")
        ok = ok_project and ok_none and ok_both and ok_worker and ok_dropdown
        self._record("C 筛选与深链", ok, "，".join(checks), started)

    async def scenario_d_permissions(self) -> None:
        """D：他人 404 / viewer 403 / 经手人改归属 403 改标题 202。"""
        started = time.monotonic()
        checks: list[str] = []

        project = await self._create_project("owner", f"权限-{uuid.uuid4().hex[:4]}")

        stranger = await self._http.get(
            f"{self.base}/api/v1/projects/{project['id']}",
            headers=self._headers("other"),
        )
        ok_stranger = stranger.status_code == 404
        checks.append(f"他人 404={stranger.status_code}")

        ok_viewer = True
        if self.viewer_invite:
            viewer = await self._http.post(
                f"{self.base}/api/v1/auth/register",
                json={
                    "email": f"proj-viewer-{uuid.uuid4().hex[:8]}@test.com",
                    "password": "TestPass123!",
                    "invite_code": self.viewer_invite,
                },
            )
            if viewer.status_code == 201:
                v_headers = {
                    "Authorization": f"Bearer {viewer.json()['tokens']['access']}"
                }
                v_resp = await self._http.get(
                    f"{self.base}/api/v1/projects", headers=v_headers
                )
                ok_viewer = v_resp.status_code == 403
                checks.append(f"viewer 403={v_resp.status_code}")
            else:
                checks.append(f"viewer 注册失败={viewer.status_code}")
                ok_viewer = False
        else:
            checks.append("viewer=未提供邀请码，跳过（集成测试覆盖）")

        # 经手人改 project_id → 403；改标题 → 200
        req = await self._create_request("owner", "D-需求", project["id"])
        await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/assign",
            headers=self._headers("owner"),
            json={"assignee_type": "user", "assignee_id": self._ids["worker"]},
        )
        await self._http.post(
            f"{self.base}/api/v1/requests/{req['id']}/accept",
            headers=self._headers("worker"),
        )
        blocked = await self._http.patch(
            f"{self.base}/api/v1/requests/{req['id']}",
            headers=self._headers("worker"),
            json={"project_id": project["id"]},
        )
        title_ok = await self._http.patch(
            f"{self.base}/api/v1/requests/{req['id']}",
            headers=self._headers("worker"),
            json={"title": "D-改标题"},
        )
        ok_split = blocked.status_code == 403 and title_ok.status_code == 200
        checks.append(f"权限分流={ok_split}（{blocked.status_code}/{title_ok.status_code}）")

        ok = ok_stranger and ok_viewer and ok_split
        self._record("D 权限边界", ok, "，".join(checks), started)

    async def scenario_e_regression(self) -> None:
        """E：s8 与 s1–s4 原样重跑（FR-014/SC-003/SC-006 零回归）。"""
        started = time.monotonic()
        checks: list[str] = []

        for script, expect in (
            ("s8_request_dispatch.py", "7 通过 / 0 失败"),
            ("s1_s4_scenarios.py", "全部通过"),
        ):
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                f"deploy/verify/{script}",
                "--base",
                self.base,
                # s1_s4 的邀请码是脚本内常量（VERIFY-MEMBER-A/B），不接受
                # --invite 参数——传了会 argparse 报错（本场景首轮就踩了）
                *(
                    []
                    if script == "s1_s4_scenarios.py"
                    else ["--invite", self.invite]
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            text = out.decode(errors="replace")
            ok = proc.returncode == 0 and expect in text
            checks.append(f"{script.split('_')[0]}={ok}")
            if not ok:
                print(text[-1500:])

        ok = all(c.endswith("=True") for c in checks)
        self._record("E 回归（s8 + s1–s4）", ok, "，".join(checks), started)

    async def run(self) -> int:
        await self.setup()
        await self.scenario_a_create_and_classify()
        await self.scenario_b_management()
        await self.scenario_c_filter()
        await self.scenario_d_permissions()
        await self.scenario_e_regression()
        await self._http.aclose()

        print()
        for r in self.results:
            print(r.line())
        failed = [r for r in self.results if not r.ok]
        print(f"\n合计 {len(self.results)} 场景：{len(self.results) - len(failed)} 通过 / {len(failed)} 失败")
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="008 项目容器真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True, help="邀请码（member）")
    parser.add_argument(
        "--viewer-invite",
        default=None,
        help="viewer 角色邀请码（可选；不提供则跳过 viewer 403 实测）",
    )
    args = parser.parse_args()
    verifier = ProjectVerifier(args.base, args.invite, viewer_invite=args.viewer_invite)
    raise SystemExit(asyncio.run(verifier.run()))


if __name__ == "__main__":
    main()
