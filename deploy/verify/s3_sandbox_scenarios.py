"""002 S1/S3/S4 acceptance against an already-running REAL deployment.

Requires httpx and the mock gateway updated with [sandbox:<scenario>] support.
No service is started/stopped, no DB fixtures/SQL are used, and .env is never read.
Creates uniquely named agents/requests/runs and retains them as acceptance evidence.

Credentials (never printed; use fresh access tokens):
  SANDBOX_VERIFY_TOKEN        owner member/admin token
  SANDBOX_VERIFY_OTHER_TOKEN  DIFFERENT owner token for cross-owner 404 checks
  SANDBOX_VERIFY_ADMIN_TOKEN  admin token for tool_denied audit verification
Alternatively, missing member tokens can be provisioned using
  SANDBOX_VERIFY_INVITE_CODE and optionally SANDBOX_VERIFY_OTHER_INVITE_CODE.
An owner admin token is also usable for audit; otherwise admin token is required
unless --skip-audit-check explicitly requests incomplete audit coverage.

Example (run from repository root, with tokens already exported):
  python deploy/verify/s3_sandbox_scenarios.py --base http://localhost:8080 \
      --artifact-fetch-container aw-api
Omit --artifact-fetch-container when presigned URLs are reachable directly.
Container mode only runs a fixed HTTP-download helper, never returned model code.
The script checks execution records, not WebSocket events, all five builtin tools,
resource stress or egress policy; those need separate acceptance checks.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import os
import secrets
import subprocess
import sys
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx


class CheckFailure(Exception):
    """A deliberately secret-free acceptance diagnostic."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def validate_execution(execution: dict[str, Any], language: str) -> None:
    require(execution.get("language") == language, "unexpected execution language")
    code = execution.get("code")
    require(isinstance(code, str) and bool(code), "execution source missing")
    require(
        hashlib.sha256(code.encode()).hexdigest() == execution.get("code_sha256"),
        "execution code_sha256 mismatch",
    )
    require(bool(execution.get("image")) and bool(execution.get("limits")),
            "execution image/limits missing")
    require(isinstance(execution.get("duration_ms"), int) and execution["duration_ms"] >= 0,
            "execution duration missing")
    require(bool(execution.get("finished_at")), "execution not finalized")


def validate_sales(content: bytes, artifact: dict[str, Any]) -> str:
    digest = hashlib.sha256(content).hexdigest()
    require(digest == artifact.get("sha256"), "artifact sha256 mismatch")
    require(len(content) == artifact.get("size_bytes"), "artifact size mismatch")
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    expected = [{"day": str(day), "sales": str(day * 10)} for day in range(1, 21)]
    require(rows == expected, "sales.csv must contain exactly 20 expected data rows")
    return digest


def fetch_in_container(container: str, url: str) -> bytes:
    # URL is stdin, not argv or console output: it contains a presigned credential.
    # A new process does not inherit the API's Authorization header.
    helper = (
        "import sys,urllib.request\n"
        "url=sys.stdin.buffer.read().decode()\n"
        "opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))\n"
        "with opener.open(url,timeout=30) as response:\n"
        "    data=response.read(1048577)\n"
        "    if len(data)>1048576: raise ValueError('artifact too large')\n"
        "    sys.stdout.buffer.write(data)\n"
    )
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", container, "python", "-I", "-c", helper],
            input=url.encode(), capture_output=True, timeout=40, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckFailure("artifact download helper unavailable/timed out") from exc
    require(result.returncode == 0, "container artifact download failed (stderr withheld)")
    return result.stdout


class Verifier:
    def __init__(self, args: argparse.Namespace, http: httpx.AsyncClient):
        self.args = args
        self.http = http
        self.base = args.base.rstrip("/")
        self.tokens: dict[str, str] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.run_ids: list[str] = []

    async def request(
        self, method: str, path: str, *, identity: str | None = "owner",
        status: int = 200, **kwargs: Any,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.tokens[identity]}"} if identity else {}
        response = await self.http.request(
            method, self.base + "/api/v1" + path, headers=headers, **kwargs,
        )
        require(response.status_code == status,
                f"{method} {path}: expected HTTP {status}, got {response.status_code}")
        return response

    async def setup(self) -> None:
        for identity, token_env in (
            ("owner", "SANDBOX_VERIFY_TOKEN"), ("other", "SANDBOX_VERIFY_OTHER_TOKEN"),
        ):
            token = os.getenv(token_env)
            if not token:
                invite = os.getenv("SANDBOX_VERIFY_OTHER_INVITE_CODE") if identity == "other" else None
                invite = invite or os.getenv("SANDBOX_VERIFY_INVITE_CODE")
                require(bool(invite), f"set {token_env} or SANDBOX_VERIFY_INVITE_CODE")
                response = await self.request(
                    "POST", "/auth/register", identity=None, status=201,
                    json={"email": f"sandbox-verify-{uuid.uuid4().hex}@example.test",
                          "password": secrets.token_urlsafe(24) + "Aa1!", "invite_code": invite},
                )
                token = response.json()["tokens"]["access"]
            self.tokens[identity] = token
            response = await self.request("GET", "/auth/me", identity=identity)
            self.users[identity] = response.json()
        require(self.users["owner"]["id"] != self.users["other"]["id"],
                "owner and other credentials must identify different users")
        require(self.users["owner"]["role"] in {"member", "admin"},
                "owner credential must have member/admin role")
        if not self.args.skip_audit_check:
            admin = os.getenv("SANDBOX_VERIFY_ADMIN_TOKEN")
            if not admin and self.users["owner"]["role"] == "admin":
                admin = self.tokens["owner"]
            require(bool(admin), "set SANDBOX_VERIFY_ADMIN_TOKEN or explicitly --skip-audit-check")
            self.tokens["admin"] = admin
            response = await self.request("GET", "/auth/me", identity="admin")
            require(response.json()["role"] == "admin", "audit credential must be admin")
        await self.request("GET", "/tools", identity=None, status=401)
        catalog = (await self.request("GET", "/tools")).json()
        names = {tool["name"] for tool in catalog["items"]}
        require(names == {"web_search", "read_url", "calculate", "run_python", "run_shell"},
                "enabled tool catalog does not match the five 002 tools")

    async def create_run(
        self, scenario: str, tools: list[str], *, agent_options: dict[str, Any] | None = None,
        egress_rules: list[dict[str, Any]] | None = None,
    ) -> str:
        agent = (await self.request(
            "POST", "/agents", status=201, json={
                "name": f"sandbox-verify-{scenario}-{uuid.uuid4().hex[:8]}",
                "system_prompt": "Use the requested tool, then report its actual result.",
                "model": self.args.model, "temperature": 0, "tools": tools,
                "max_steps": 8, "max_run_seconds": max(60, int(self.args.timeout)),
                "cost_limit_cents": 10000,
                **(agent_options or {}),
            },
        )).json()
        require(agent["tools"] == tools, "agent tool grant was not persisted")
        for rule in egress_rules or []:
            await self.request("POST", f"/agents/{agent['id']}/egress-rules", status=201, json=rule)
        request = (await self.request(
            "POST", "/requests", status=201, json={
                "title": f"002 sandbox {scenario} {uuid.uuid4().hex[:8]}",
                "description": f"[sandbox:{scenario}] Deterministic 002 sandbox acceptance.",
                "expected_output": "Actual sandbox execution evidence, not a simulated result.",
            },
        )).json()
        assigned = (await self.request(
            "POST", f"/requests/{request['id']}/assign", status=202,
            json={"assignee_type": "agent", "assignee_id": agent["id"]},
        )).json()
        run_id = assigned["run_id"]
        self.run_ids.append(run_id)
        print(f"INFO {scenario}: run={run_id}", flush=True)
        return run_id

    async def execute(self, scenario: str, tools: list[str]) -> dict[str, Any]:
        run_id = await self.create_run(scenario, tools)
        return await self.wait_run(run_id)

    async def wait_run(
        self, run_id: str, *, expected_status: str = "completed",
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline:
            run = (await self.request("GET", f"/runs/{run_id}")).json()
            if run["status"] in {"completed", "failed", "timeout", "cancelled", "needs_input"}:
                require(run["status"] == expected_status,
                        f"terminal run status={run['status']} expected={expected_status} (inspect run ID above)")
                return run
            await asyncio.sleep(self.args.poll_interval)
        raise CheckFailure("run poll deadline exceeded (inspect run ID above)")

    async def executions(self, run: dict[str, Any], count: int, language: str) -> list[dict[str, Any]]:
        summaries = run.get("executions", [])
        require(len(summaries) == count, f"expected {count} sandbox execution(s), got {len(summaries)}")
        details = []
        for summary in sorted(summaries, key=lambda item: item["step_seq"]):
            require(not {"code", "stdout", "stderr"}.intersection(summary),
                    "verbose execution data leaked into run summary")
            execution = (await self.request(
                "GET", f"/runs/{run['id']}/executions/{summary['id']}",
            )).json()
            validate_execution(execution, language)
            details.append(execution)
        return details

    def answer(self, run: dict[str, Any], marker: str) -> None:
        answer = (run.get("result") or {}).get("answer", "")
        require(marker in answer and "SANDBOX_SCENARIO_ERROR" not in answer,
                "mock final answer did not confirm the observed tool result")

    async def python_sum(self) -> None:
        run = await self.execute("python_sum", ["run_python"])
        execution, = await self.executions(run, 1, "python")
        require(execution["status"] == "ok" and execution["exit_code"] == 0, "Python sum failed")
        require((execution["stdout"] or "").strip() == "338350", "incorrect Python sum stdout")
        self.answer(run, "SANDBOX_PYTHON_SUM_OK: 338350")
        elapsed_ms = (
            datetime.fromisoformat(execution["finished_at"])
            - datetime.fromisoformat(execution["created_at"])
        ).total_seconds() * 1000
        require(0 <= elapsed_ms <= self.args.max_execution_ms, "SC-001 full execution latency exceeded")
        print(f"PASS S1 Python sum=338350 process_ms={execution['duration_ms']} elapsed_ms={elapsed_ms:.0f}")

    async def shell(self) -> None:
        run = await self.execute("shell", ["run_shell"])
        execution, = await self.executions(run, 1, "shell")
        require(execution["status"] == "ok" and execution["exit_code"] == 0, "shell failed")
        require((execution["stdout"] or "").strip() == "sandbox-shell-ok", "shell stdout mismatch")
        self.answer(run, "SANDBOX_SHELL_OK: sandbox-shell-ok")
        print("PASS S1 real shell tool")

    async def failure(self) -> None:
        run = await self.execute("failure", ["run_python"])
        failed, recovered = await self.executions(run, 2, "python")
        require(failed["status"] == "failed" and failed["error_category"] == "exit_code"
                and failed["exit_code"] == 1, "controlled failure was not correctly classified")
        require("sandbox-controlled-error" in (failed.get("stderr") or ""),
                "controlled failure stderr missing")
        require(recovered["status"] == "ok" and recovered["exit_code"] == 0
                and (recovered["stdout"] or "").strip() == "sandbox-recovered",
                "next-round recovery failed")
        self.answer(run, "SANDBOX_FAILURE_OK: sandbox-recovered")
        print("PASS S1 exit_code=1 + stderr + next-round correction")

    async def artifacts(self) -> None:
        run = await self.execute("artifacts", ["run_python"])
        execution, = await self.executions(run, 1, "python")
        require(execution["status"] == "ok" and execution["exit_code"] == 0, "artifact script failed")
        self.answer(run, "SANDBOX_ARTIFACTS_OK: sales.csv: 20 rows")
        matches = [item for item in run.get("artifacts", []) if item["filename"] == "sales.csv"]
        require(len(matches) == 1, "expected exactly one sales.csv artifact")
        artifact, = matches
        require(any(item["id"] == artifact["id"] for item in execution["artifacts"]),
                "artifact not linked to its execution")
        path = f"/runs/{run['id']}/artifacts/{artifact['id']}"
        await self.request("GET", f"/runs/{run['id']}", identity="other", status=404)
        await self.request(
            "GET", f"/runs/{run['id']}/executions/{execution['id']}", identity="other", status=404,
        )
        await self.request("GET", path, identity="other", status=404)
        await self.request("GET", path, identity=None, status=401)
        redirect = await self.request("GET", path, status=302)
        url = redirect.headers.get("location", "")
        require(urlsplit(url).scheme in {"http", "https"} and bool(urlsplit(url).hostname),
                "invalid artifact redirect")
        if self.args.artifact_fetch_container:
            content = await asyncio.to_thread(fetch_in_container, self.args.artifact_fetch_container, url)
        else:
            # Never send the owner's bearer token to object storage.
            async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as download:
                response = await download.get(url)
            require(response.status_code == 200, "direct artifact download failed; try --artifact-fetch-container")
            content = response.content
        digest = validate_sales(content, artifact)
        print(f"PASS S4 sales.csv rows=20 sha256={digest}; cross-owner run/execution/artifact=404")

    async def denied(self) -> None:
        run = await self.execute("denied", ["calculate"])
        await self.executions(run, 0, "shell")
        steps = [step for step in run["steps"] if step["kind"] == "tool"]
        require(len(steps) == 1 and steps[0]["tool_name"] == "run_shell", "forced unauthorized call missing")
        require(not run.get("artifacts"), "denied call unexpectedly produced artifacts")
        self.answer(run, "SANDBOX_DENIED_CONFIRMED")
        if self.args.skip_audit_check:
            print("SKIP S3 tool_denied audit (--skip-audit-check); not full audit acceptance")
        else:
            response = await self.request(
                "GET", "/audit-logs", identity="admin",
                params={"action": "tool_denied", "user_id": self.users["owner"]["id"], "page_size": 100},
            )
            matches = [item for item in response.json()["items"]
                       if item.get("target_id") == run["id"]
                       and (item.get("detail") or {}).get("tool_name") == "run_shell"]
            require(bool(matches), "tool_denied audit entry missing for exact run")
            print("PASS S3 exact-run tool_denied audit")
        print("PASS S3 calculate-only agent rejected run_shell; no execution/artifact")

    async def no_tools(self) -> None:
        run = await self.execute("no_tools", [])
        await self.executions(run, 0, "python")
        require(not any(step["kind"] == "tool" for step in run["steps"]),
                "tools=[] unexpectedly generated a tool step")
        require(not run.get("artifacts"), "tools=[] unexpectedly produced artifacts")
        self.answer(run, "SANDBOX_NO_TOOLS_OK")
        print("PASS S3 tools=[] has no tool calls, executions or artifacts")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--base", default=os.getenv("SANDBOX_VERIFY_BASE", "http://localhost:8080"))
    result.add_argument("--model", default=os.getenv("SANDBOX_VERIFY_MODEL", "gpt-4o-mini"))
    result.add_argument("--timeout", type=float, default=180, help="Per-run poll deadline in seconds")
    result.add_argument("--poll-interval", type=float, default=0.5)
    result.add_argument("--max-execution-ms", type=int, default=5000, help="S1/SC-001 sandbox duration cap")
    result.add_argument("--artifact-fetch-container", help="Fetch internal presigned URL using docker exec")
    result.add_argument("--skip-audit-check", action="store_true", help="Explicitly omit admin-only audit assertion")
    return result


async def main(argv: list[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    if args.timeout <= 0 or args.timeout > 86400 or args.poll_interval <= 0 or args.max_execution_ms <= 0:
        ap.error("timeouts/interval/latency must be positive and timeout <= 86400")
    if urlsplit(args.base).scheme not in {"http", "https"}:
        ap.error("--base must be an http(s) URL")
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as http:
        verifier = Verifier(args, http)
        try:
            await verifier.setup()
        except CheckFailure as exc:
            print(f"FAIL setup: {exc}")
            return 1
        except Exception as exc:
            print(f"FAIL setup: {type(exc).__name__} (details withheld to protect credentials)")
            return 1
        failures = 0
        for name in ("python_sum", "shell", "failure", "artifacts", "denied", "no_tools"):
            try:
                await getattr(verifier, name)()
            except CheckFailure as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__} (details withheld to protect credentials)")
        coverage = "; audit explicitly skipped" if args.skip_audit_check else ""
        print(f"SUMMARY scenarios=6 failed={failures}{coverage}; evidence runs retained")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
