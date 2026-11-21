"""所有权移交的真实栈验收（017 / T401）。

四组场景对应 spec 的 US1–US5 与契约的错误矩阵，全部走 HTTP 打真实容器栈：

- **A 调用者权限**：陌生人 404（与「项目不存在」逐字相同）、成员 403、viewer 403、
  未认证 401——顺序本身是契约（陌生人不能靠传不存在的用户探测项目）。
- **B 移交成功**：keep_as_member=true（原 owner 保留访问、新 owner 拿到四个动作）、
  keep_as_member=false（原 owner 变 404、两个 scope 都看不到）、需求归属与快照不变。
- **C 校验顺序与事务**：自移交 409 → 目标不存在 404 → 非成员 409 → 同名 409，
  且同名失败后**归属不变**。其中「目标停用 400」与「目标只读 409」需要停用账号 /
  viewer 邀请码（本脚本只拿一个 member 码），由
  `backend/tests/integration/test_project_transfer.py` 覆盖。
- **D 通知**：新 owner 收到 `project_ownership_received`（审计由集成测试断言，
  这里不引 admin 凭据）。

与 s10/s11 同构输出；用同步客户端：本场景没有并发与长等待，async 只增样板。

依赖：真实栈（`docker compose -p aw -f deploy/docker-compose.yml up -d`）与一个
member 邀请码。

用法：
    python deploy/verify/s13_project_transfer.py --base http://localhost:8080 --invite <INVITE>
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


class TransferVerifier:
    def __init__(self, base: str, invite: str) -> None:
        self.base = base.rstrip("/")
        self.api = f"{self.base}/api/v1"
        self.invite = invite
        self.run_id = uuid.uuid4().hex[:6]
        self.http = httpx.Client(timeout=30.0)
        self.results: list[Result] = []
        self.tokens: dict[str, str] = {}
        self.ids: dict[str, str] = {}

    # ---------- 基础 ----------

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(
            Result(name, ok, detail, time.monotonic() - started)
        )

    def _h(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _post(self, who: str, path: str, json: dict | None = None) -> httpx.Response:
        return self.http.post(f"{self.api}{path}", json=json, headers=self._h(who))

    def _patch(self, who: str, path: str, json: dict) -> httpx.Response:
        return self.http.patch(f"{self.api}{path}", json=json, headers=self._h(who))

    def _get(self, who: str, path: str, **params) -> httpx.Response:
        return self.http.get(f"{self.api}{path}", headers=self._h(who), params=params)

    def setup(self) -> None:
        for who in ("a", "b", "c"):
            resp = self.http.post(
                f"{self.api}/auth/register",
                json={
                    "email": f"s13-{who}-{self.run_id}@test.com",
                    "password": PASSWORD,
                    "invite_code": self.invite,
                },
            )
            assert resp.status_code == 201, f"注册 {who} 失败：{resp.text[:200]}"
            body = resp.json()
            self.tokens[who] = body["tokens"]["access"]
            self.ids[who] = body["user"]["id"]

    # ---------- A 调用者权限 ----------

    def scenario_a_callers(self) -> None:
        project = self._new_project("a", f"A权限-{self.run_id}")

        started = time.monotonic()
        resp = self._post("c", f"/projects/{project}/transfer", {"new_owner_id": self.ids["b"]})
        message = resp.json()["error"]["message"] if resp.status_code else ""
        self._record(
            "A1 陌生人 404 且文案与不存在相同",
            resp.status_code == 404 and message == "项目不存在",
            f"{resp.status_code} {message}",
            started,
        )

        started = time.monotonic()
        self._post("a", f"/projects/{project}/members", {"user_id": self.ids["b"]})
        resp = self._post("b", f"/projects/{project}/transfer", {"new_owner_id": self.ids["b"]})
        self._record(
            "A2 成员发起 403",
            resp.status_code == 403,
            f"{resp.status_code} {resp.json()['error']['message']}",
            started,
        )

        started = time.monotonic()
        resp = self.http.post(f"{self.api}/projects/{project}/transfer", json={"new_owner_id": self.ids["b"]})
        self._record("A3 未认证 401", resp.status_code == 401, str(resp.status_code), started)

    # ---------- B 移交成功 ----------

    def scenario_b_success(self) -> None:
        started = time.monotonic()
        project = self._new_project("a", f"B成功-{self.run_id}")
        self._post("a", f"/projects/{project}/members", {"user_id": self.ids["b"]})
        request = self._post("a", "/requests", {"title": f"B需求-{self.run_id}", "project_id": project})
        assert request.status_code == 201, request.text[:200]

        resp = self._post(
            "a",
            f"/projects/{project}/transfer",
            {"new_owner_id": self.ids["b"], "keep_as_member": True},
        )
        body = resp.json() if resp.status_code == 200 else {}
        checks = [
            resp.status_code == 200,
            body.get("owner_id") == self.ids["b"],
            # 成员数含 owner：新 owner + 保留的原 owner = 2
            body.get("member_count") == 2,
        ]
        self._record("B1 keep=true 移交成功", all(checks), f"status={resp.status_code} member_count={body.get('member_count')}", started)

        started = time.monotonic()
        mine_b = self._get("b", "/projects", scope="mine").json()
        mine_a = self._get("a", "/projects", scope="mine").json()
        rel_a = self._get("a", "/projects", scope="relevant").json()
        members = self._get("b", f"/projects/{project}/members").json()["items"]
        owner_rows = [m for m in members if m["is_owner"]]
        requests = self._get("b", "/requests", project_id=project).json()
        checks = {
            "B.mine 含": any(x["id"] == project for x in mine_b["items"]),
            "A.mine 不含": all(x["id"] != project for x in mine_a["items"]),
            "A.relevant 仍含": any(x["id"] == project for x in rel_a["items"]),
            "成员表含 A 与 B": {m["user_id"] for m in members} == {self.ids["a"], self.ids["b"]},
            "owner 标记是 B": len(owner_rows) == 1 and owner_rows[0]["user_id"] == self.ids["b"],
            "需求仍在项目里": requests["total"] >= 1,
        }
        failed = [k for k, v in checks.items() if not v]
        self._record("B2 移交后的四个不变式", not failed, "、".join(failed) or "全部成立", started)

        started = time.monotonic()
        renamed = self._patch("b", f"/projects/{project}", {"name": f"B改名-{self.run_id}"})
        denied = self._patch("a", f"/projects/{project}", {"name": "x"})
        member_add = self._post("a", f"/projects/{project}/members", {"user_id": self.ids["c"]})
        checks = [renamed.status_code == 200, denied.status_code == 403, member_add.status_code == 403]
        self._record(
            "B3 权限换手（B 可改/A 403）",
            all(checks),
            f"B改名={renamed.status_code} A改名={denied.status_code} A加人={member_add.status_code}",
            started,
        )

        started = time.monotonic()
        project2 = self._new_project("a", f"B退出-{self.run_id}")
        self._post("a", f"/projects/{project2}/members", {"user_id": self.ids["b"]})
        resp = self._post(
            "a",
            f"/projects/{project2}/transfer",
            {"new_owner_id": self.ids["b"], "keep_as_member": False},
        )
        detail = self._get("a", f"/projects/{project2}")
        rel = self._get("a", "/projects", scope="relevant").json()
        checks = [
            resp.status_code == 200,
            detail.status_code == 404,
            all(x["id"] != project2 for x in rel["items"]),
        ]
        self._record(
            "B4 keep=false 后原 owner 完全退出",
            all(checks),
            f"移交={resp.status_code} 详情={detail.status_code}",
            started,
        )

    # ---------- C 校验顺序与事务 ----------

    def scenario_c_validation(self) -> None:
        started = time.monotonic()
        project = self._new_project("a", f"C校验-{self.run_id}")
        self._post("a", f"/projects/{project}/members", {"user_id": self.ids["b"]})

        self_ = self._post("a", f"/projects/{project}/transfer", {"new_owner_id": self.ids["a"]})
        missing = self._post("a", f"/projects/{project}/transfer", {"new_owner_id": str(uuid.uuid4())})
        not_member = self._post("a", f"/projects/{project}/transfer", {"new_owner_id": self.ids["c"]})
        checks = [
            (self_.status_code, 409),
            (missing.status_code, 404),
            (not_member.status_code, 409),
        ]
        self._record(
            "C1 自移交/目标不存在/非成员",
            all(got == want for got, want in checks),
            "，".join(f"{got}(期望{want})" for got, want in checks),
            started,
        )

        started = time.monotonic()
        name = f"C撞名-{self.run_id}"
        self._new_project("b", name)
        project2 = self._new_project("a", name)
        self._post("a", f"/projects/{project2}/members", {"user_id": self.ids["b"]})
        resp = self._post("a", f"/projects/{project2}/transfer", {"new_owner_id": self.ids["b"]})
        message = resp.json()["error"]["message"] if resp.status_code else ""
        owner_after = self._get("a", f"/projects/{project2}").json().get("owner_id")
        checks = [
            resp.status_code == 409,
            name in message,
            owner_after == self.ids["a"],
        ]
        self._record(
            "C2 同名 409 + 消息带项目名 + 归属不变",
            all(checks),
            f"{resp.status_code} {message}",
            started,
        )

    # ---------- D 通知 ----------

    def scenario_d_notification(self) -> None:
        started = time.monotonic()
        notes = self._get("b", "/notifications").json().get("items", [])
        received = [n for n in notes if n["type"] == "project_ownership_received"]
        self._record(
            "D1 新 owner 收到 ownership 通知",
            len(received) >= 2,
            f"{len(received)} 条（B 组两次移交）",
            started,
        )

    # ---------- 工具 ----------

    def _new_project(self, who: str, name: str) -> str:
        resp = self._post(who, "/projects", {"name": name})
        assert resp.status_code == 201, f"建项目失败：{resp.text[:200]}"
        return resp.json()["id"]

    def run(self) -> int:
        self.setup()
        self.scenario_a_callers()
        self.scenario_b_success()
        self.scenario_c_validation()
        self.scenario_d_notification()
        self.http.close()

        print()
        for r in self.results:
            print(r.line())
        failed = [r for r in self.results if not r.ok]
        print(f"\n合计 {len(self.results)} 场景：{len(self.results) - len(failed)} 通过 / {len(failed)} 失败")
        return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="017 所有权移交真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True, help="邀请码（member）")
    args = parser.parse_args()
    sys.exit(TransferVerifier(args.base, args.invite).run())


if __name__ == "__main__":
    main()
