"""MCP stdio 传输（024）的真实栈验收。

场景：

- **A 创建**：admin 建一个 stdio server（command=python3, args=[fake server]）
  → 201，`transport=stdio`，`endpoint_url` 由服务端补成 `stdio://python3`。
- **B 校验链路**：POST 之后 worker 跑 `validate_external_server` 任务，
  对 stdio 行启动子进程握手 + tools/list → 轮询到
  `connection_status=passed`、`discovered_count=1`。
- **C 拒绝**：非白名单命令 422；http 行带 command 422。

前置：fake server 要存在于 **worker 容器**内（脚本无法自己 cp）：

    docker cp backend/tests/fixtures/fake_mcp_stdio_server.py \
        aw-worker:/tmp/fake_mcp_stdio_server.py

用法：
    python deploy/verify/s20_stdio_transport.py --base http://localhost:8080 \
        --invite <INVITE> --admin-email <ADMIN> --admin-password <PWD>
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

PASSWORD = "TestPass123!"
FAKE_SERVER = "/tmp/fake_mcp_stdio_server.py"


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


class StdioVerifier:
    def __init__(self, base: str, invite: str, admin_email: str, admin_password: str):
        self.api = f"{base.rstrip('/')}/api/v1"
        self.invite = invite
        self.admin_email = admin_email
        self.admin_password = admin_password
        self.run_id = uuid.uuid4().hex[:6]
        self.http = httpx.Client(timeout=30.0)
        self.results: list[Result] = []
        self.token = ""

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _post(self, path: str, json: dict | None = None) -> httpx.Response:
        return self.http.post(f"{self.api}{path}", json=json, headers=self._h())

    def _get(self, path: str) -> httpx.Response:
        return self.http.get(f"{self.api}{path}", headers=self._h())

    def _record(self, name: str, ok: bool, detail: str, started: float) -> None:
        self.results.append(Result(name, ok, detail, time.monotonic() - started))

    def setup(self) -> None:
        login = self.http.post(
            f"{self.api}/auth/login",
            json={"email": self.admin_email, "password": self.admin_password},
        )
        if login.status_code != 200:
            raise SystemExit(
                f"admin 登录失败（{login.status_code}）：需要 --admin-email/--admin-password"
            )
        self.token = login.json()["tokens"]["access"]

    def _payload(self, **overrides) -> dict:
        body = {
            "name": f"s20-stdio-{uuid.uuid4().hex[:5]}",
            "description": "验收用 stdio MCP server",
            "transport": "stdio",
            "command": "python3",
            "args": [FAKE_SERVER],
            "maintainer": "verify",
            "auth_mode": "none",
        }
        body.update(overrides)
        return body

    def _wait_validation(self, server_id: str, timeout: float = 40.0) -> dict:
        deadline = time.monotonic() + timeout
        last: dict = {}
        while time.monotonic() < deadline:
            detail = self._get(f"/external-tool-servers/{server_id}")
            if detail.status_code == 200:
                last = detail.json().get("last_validation") or {}
                if last.get("connection_status") in ("passed", "failed"):
                    return last
            time.sleep(2)
        return last

    def run(self) -> int:
        self.setup()

        started = time.monotonic()
        created = self._post("/external-tool-servers", self._payload())
        body = created.json() if created.status_code == 201 else {}
        ok = (
            created.status_code == 201
            and body.get("transport") == "stdio"
            and str(body.get("endpoint_url", "")).startswith("stdio://")
            and body.get("command") == "python3"
        )
        self._record(
            "A1 创建 stdio server（占位 endpoint 由服务端补）",
            ok,
            f"{created.status_code} {body.get('endpoint_url')}",
            started,
        )
        server_id = body.get("id")

        if server_id:
            started = time.monotonic()
            validation = self._wait_validation(server_id)
            ok = (
                validation.get("connection_status") == "passed"
                and validation.get("discovered_count") == 1
            )
            self._record(
                "B1 worker 探活 stdio：握手 + tools/list 发现 1 个工具",
                ok,
                f"status={validation.get('connection_status')} "
                f"count={validation.get('discovered_count')} "
                f"err={validation.get('safe_error')}",
                started,
            )

        started = time.monotonic()
        rejected_cmd = self._post("/external-tool-servers", self._payload(command="/bin/sh"))
        mixed = self._post(
            "/external-tool-servers",
            {
                "name": f"s20-http-{uuid.uuid4().hex[:5]}",
                "description": "混用",
                "transport": "http",
                "endpoint_url": "https://example.com/mcp",
                "command": "npx",
                "maintainer": "verify",
                "auth_mode": "none",
            },
        )
        self._record(
            "C1 非白名单命令 422 / http 带 command 422",
            rejected_cmd.status_code == 422 and mixed.status_code == 422,
            f"cmd={rejected_cmd.status_code} mixed={mixed.status_code}",
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
    parser = argparse.ArgumentParser(description="024 stdio 传输真实栈验收")
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    args = parser.parse_args()
    sys.exit(
        StdioVerifier(
            args.base, args.invite, args.admin_email, args.admin_password
        ).run()
    )


if __name__ == "__main__":
    main()
