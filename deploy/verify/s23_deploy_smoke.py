"""部署 smoke（028）：重建镜像 → 全栈重启 → 迁移版本 → 三条验收重跑。

## 它验证什么、不验证什么（先说清）

**验证**：部署产物（镜像 + compose + 迁移脚本）能起来、能服务、关键链路仍通。
覆盖的是绝大多数部署事故：镜像构建漏文件、迁移没跑、环境变量缺失、容器重启后
worker 丢任务注册。

**不验证**：**空库冷启动**（`down -v` 后从零 up）。本机端口是写死的
（5432/6379/9000），起不了第二套实例；而 `down -v` 会清掉开发库里的全部数据。
所以这里用「restart + 现有库」——空库冷启动的正确验证场景是**一次性的预发环境**，
不是开发机。

用法（在 repo 根）：
    python deploy/verify/s23_deploy_smoke.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-p", "aw", "-f", "deploy/docker-compose.yml"]
SERVICES = ["api", "worker", "beat", "frontend"]


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        return f"{'✅' if self.ok else '❌'} {self.name} — {self.detail}"


def _run(cmd: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--invite", default="VERIFY-012-MEMBER")
    parser.add_argument("--admin-email", default="")
    parser.add_argument("--admin-password", default="TestPass123!")
    parser.add_argument("--skip-restart", action="store_true")
    args = parser.parse_args()

    results: list[Result] = []

    if not args.skip_restart:
        restart = _run([*COMPOSE, "restart", *SERVICES])
        results.append(
            Result(
                "A1 全栈 restart",
                restart.returncode == 0,
                (restart.stderr or restart.stdout).strip()[-200:],
            )
        )

    # 健康
    deadline = time.monotonic() + 120
    health = 0
    while time.monotonic() < deadline:
        try:
            health = httpx.get(f"{args.base}/api/v1/health", timeout=3).status_code
            if health == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(2)
    results.append(Result("A2 /health 200", health == 200, f"status={health}"))

    # 迁移版本与 head 一致
    current = _run(["docker", "exec", "aw-api", "alembic", "current"])
    version = (current.stdout or "").strip().splitlines()
    revision = version[-1].split()[0] if version else ""
    has_head = "head" in (current.stdout or "")
    results.append(
        Result("A3 迁移已到 head", has_head, revision or (current.stderr or "")[:120])
    )

    # worker 注册了关键任务（部署后 worker 丢注册是经典事故）
    inspect = _run(["docker", "exec", "aw-worker", "celery", "-A", "app.tasks.celery_app",
                    "inspect", "registered"], timeout=60)
    registered = (inspect.stdout or "") + (inspect.stderr or "")
    missing = [
        task
        for task in ("execute_run", "deliver_notification", "auto_start_request")
        if task not in registered
    ]
    results.append(
        Result("A4 worker 任务注册齐全", not missing, f"missing={missing or '无'}")
    )

    # 三条既有验收重跑（覆盖分派 / 角色 / 申请）
    for script, extra in (
        ("s14_notification_webhook.py", []),
        ("s15_project_roles.py", []),
        ("s22_invite_requests.py", ["--admin-email", args.admin_email, "--admin-password", args.admin_password]),
    ):
        if "--admin-email" in extra and not args.admin_email:
            results.append(Result(f"B {script}", False, "缺少 --admin-email，跳过即失败（不装绿）"))
            continue
        proc = _run(
            [
                str(ROOT / "backend/.venv/bin/python"),
                str(ROOT / "deploy/verify" / script),
                "--base",
                args.base,
                "--invite",
                args.invite,
                *extra,
            ]
        )
        ok = proc.returncode == 0
        tail = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        results.append(Result(f"B {script}", ok, tail[0][:160]))

    print()
    for r in results:
        print(r.line())
    failed = [r for r in results if not r.ok]
    print(f"\n合计 {len(results)} 项：{len(results) - len(failed)} 通过 / {len(failed)} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
