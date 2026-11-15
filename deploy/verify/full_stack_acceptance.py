"""Protected 002 HTTP acceptance on an explicitly CI-owned real stack.

No mocks for application, DB, Redis, Docker, object storage or WebSockets. The
model gateway is deterministic; actual executors must produce every observation.
No SQL, token minting, service restarts, .env reads or acceptance skips here.
Results contain only bounded, allowlisted measurements, never HTTP bodies/tokens.
"""
# ruff: noqa: BLE001
# Broad catches are deliberate at secret-safe acceptance/reporting boundaries.
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from s3_sandbox_scenarios import CheckFailure, Verifier, parser, require
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

TERMINAL = {"completed", "failed", "timeout", "cancelled", "needs_input"}


def percentile95(samples: list[float]) -> float:
    require(bool(samples) and all(math.isfinite(x) and x >= 0 for x in samples),
            "P95 requires finite nonnegative measurements")
    return sorted(samples)[math.ceil(len(samples) * .95) - 1]


def performance_verdict(baseline: list[float], loaded: list[float], failures: int) -> dict[str, Any]:
    """No warmup/sample exclusions, retries, clipping, rounding or relaxed ratios."""
    baseline_p95, load_p95 = percentile95(baseline), percentile95(loaded)
    require(baseline_p95 > 0, "baseline clock resolution insufficient")
    ratio = load_p95 / baseline_p95
    return {"baseline_p95_ms": baseline_p95, "load_p95_ms": load_p95,
            "p95_ratio": ratio, "request_failures": failures,
            "baseline_samples_ms": baseline, "load_samples_ms": loaded,
            "passed": failures == 0 and ratio <= 1.2}


def interval_overlap(executions: list[dict[str, Any]]) -> int:
    points = []
    for item in executions:
        points.extend([(datetime.fromisoformat(item["created_at"]), 1),
                       (datetime.fromisoformat(item["finished_at"]), -1)])
    active = peak = 0
    for _, delta in sorted(points, key=lambda point: (point[0], point[1])):
        active += delta
        peak = max(peak, active)
    return peak


class FullVerifier(Verifier):
    def __init__(self, args: argparse.Namespace, http: httpx.AsyncClient):
        super().__init__(args, http)
        self.evidence: list[dict[str, Any]] = []
        self.metrics: dict[str, Any] = {}

    async def setup(self) -> None:
        # Only the ephemeral bootstrap knows this password; API logins issue real
        # access tokens. Tokens never enter argv, files or result artifacts.
        password = os.environ.get("AW_VERIFY_USER_PASSWORD", "")
        require(os.environ.get("AW_VERIFY_CI") == "1" and bool(password),
                "full acceptance requires CI-owned fixture credentials")
        for identity, env_name in (("owner", "SANDBOX_VERIFY_TOKEN"),
                                   ("other", "SANDBOX_VERIFY_OTHER_TOKEN"),
                                   ("admin", "SANDBOX_VERIFY_ADMIN_TOKEN")):
            response = await self.request("POST", "/auth/login", identity=None,
                                          json={"email": f"{identity}@002-ci.example.com",
                                                "password": password})
            os.environ[env_name] = response.json()["tokens"]["access"]
        await super().setup()
        require(self.users["owner"]["role"] == self.users["other"]["role"] == "member",
                "ownership tests must use normal members, not administrator bypasses")

    async def audit(self, action: str, target: str) -> list[dict[str, Any]]:
        # New fixture DB; server sorts newest first. Each invocation checks its
        # exact target immediately; no historical unrelated entry can satisfy it.
        response = await self.request("GET", "/audit-logs", identity="admin",
                                      params={"action": action, "page_size": 100})
        return [item for item in response.json()["items"] if item.get("target_id") == target]

    async def checked_execution(self, run: dict, *, category: str | None = None) -> dict:
        execution, = await self.executions(run, 1, "python")
        steps = [step for step in run["steps"] if step.get("tool_name") == "run_python"]
        require(len(steps) == 1, "one persisted sandbox tool step required")
        if category is None:
            require(execution["status"] == "ok" and execution["exit_code"] == 0,
                    "sandbox did not complete successfully")
        else:
            require(execution["error_category"] == category and execution["status"] != "ok",
                    f"failure must be classified {category}")
            require(steps[0]["status"] == "failed", "failed execution has fake-success tool step")
            message = execution.get("error_message") or ""
            require(bool(message) and "未知错误" not in message and any(ord(c) > 127 for c in message),
                    "failure needs readable localized classification")
            require((steps[0].get("output") or {}).get("ok") is False,
                    "failed tool result must explicitly set ok=false")
        require(bool(await self.audit("sandbox_exec", execution["id"])),
                "exact-execution sandbox audit missing")
        self.evidence.append({"run_id": run["id"], "execution_id": execution["id"],
                              "status": execution["status"], "error_category": execution.get("error_category"),
                              "duration_ms": execution["duration_ms"], "exit_code": execution["exit_code"],
                              "cpu_ms": execution.get("cpu_ms"), "mem_peak_bytes": execution.get("mem_peak_bytes")})
        return execution

    async def builtins(self) -> None:
        for name in ("calculate", "web_search", "read_url"):
            run = await self.execute(name, [name])
            await self.executions(run, 0, "python")
            steps = [step for step in run["steps"] if step["kind"] == "tool"]
            require(len(steps) == 1 and steps[0]["tool_name"] == name
                    and steps[0]["status"] == "completed", f"{name} executor unavailable")
            output = steps[0].get("output") or {}
            if name == "calculate":
                require(output.get("result") == 338350, "calculator did not produce exact sum")
            elif name == "web_search":
                require(any(item.get("title") == "002 controlled search result"
                            for item in output.get("results", [])),
                        "search did not actually call configured HTTP search service")
            else:
                require(output.get("status") == 200 and "Example Domain" in output.get("text", ""),
                        "read_url did not actually fetch public HTML")
            self.answer(run, f"SANDBOX_{name.upper()}_OK")

    async def denial_threshold(self) -> None:
        run_id = await self.create_run("denied_threshold", ["calculate"], agent_options={"max_steps": 20})
        run = await self.wait_run(run_id, expected_status="failed")
        tools = [step for step in run["steps"] if step["kind"] == "tool"]
        require(len(tools) == 5 and all(step["tool_name"] == "run_shell"
                                      and (step.get("output") or {}).get("denied") is True
                                      and (step.get("output") or {}).get("ok") is False for step in tools),
                "deny threshold must stop after exactly five unauthorized calls")
        require(not run["executions"] and not run["artifacts"], "denied tools executed side effects")
        require(len(await self.audit("tool_denied", run_id)) == 5,
                "all five denied calls must have exact-run audit facts")
        require(bool(run.get("error")), "threshold termination explanation missing")

    async def output_limits(self) -> None:
        run = await self.execute("output", ["run_python"])
        execution = await self.checked_execution(run)
        tail = b"OUTPUT_TAIL\n"
        require(execution["stdout_truncated"] is True
                and execution["stdout_full_bytes"] == 3 * 1024 * 1024 + len(tail),
                "original stdout byte count/truncation marker incorrect")
        expected = b"X" * (1024 * 1024 - len(tail)) + tail
        require(execution["stdout"].encode() == expected, "stdout must preserve exact final MiB")
        require("BINARY_STDERR" in execution["stderr"] and "�" in execution["stderr"],
                "invalid UTF-8 stderr did not degrade readably")
        self.answer(run, "SANDBOX_OUTPUT_OK")

    async def download(self, run: dict, artifact: dict) -> bytes:
        redirect = await self.request("GET", f"/runs/{run['id']}/artifacts/{artifact['id']}", status=302)
        url = redirect.headers.get("location", "")
        require(urlsplit(url).scheme in {"http", "https"}, "object download redirect missing")
        # The runner fetches the exact public redirect, no URL rewriting, no
        # docker-exec workaround and no owner's authorization sent to MinIO.
        async with (
            httpx.AsyncClient(timeout=30, trust_env=False) as storage,
            storage.stream("GET", url) as response,
        ):
            require(response.status_code == 200, "runner cannot fetch exact presigned redirect")
            chunks, length = [], 0
            async for chunk in response.aiter_bytes():
                length += len(chunk)
                require(length <= 20 * 1024 * 1024, "artifact exceeded per-file download ceiling")
                chunks.append(chunk)
        content = b"".join(chunks)
        require(len(content) == artifact["size_bytes"]
                and hashlib.sha256(content).hexdigest() == artifact["sha256"],
                "artifact size/hash differs from persisted content")
        return content

    async def artifact_limits(self) -> None:
        for scenario, reason in (("artifact_files", "files"), ("artifact_file_size", "file_size"),
                                 ("artifact_total_size", "total_size")):
            run = await self.execute(scenario, ["run_python"])
            execution = await self.checked_execution(run)
            truncation = execution.get("artifact_truncated") or {}
            require(truncation.get("reason") == reason and bool(truncation.get("message")),
                    f"{reason} artifact limit lacks explicit persisted warning")
            require(bool(await self.audit("artifact_truncated", execution["id"])),
                    "artifact truncation audit missing")
            artifacts = run["artifacts"]
            require(len(artifacts) <= 50 and all(a["size_bytes"] <= 20 * 1024 * 1024 for a in artifacts)
                    and sum(a["size_bytes"] for a in artifacts) <= 50 * 1024 * 1024,
                    "artifact hard limits exceeded")
            if reason == "files":
                require(len(artifacts) == 50, "file-count test must retain exactly 50 of 60 files")
            elif reason == "file_size":
                require(len(artifacts) == 0, "oversize file must not be published")
            else:
                require(len(artifacts) == 2, "total limit must retain two 18MiB files, not silently drop all")
            for artifact in artifacts:
                content = await self.download(run, artifact)
                if reason == "files":
                    number = int(artifact["filename"].removeprefix("file-").removesuffix(".txt"))
                    require(content == str(number).encode(), "artifact source-byte mismatch")
                else:
                    require(content == b"T" * (18 * 1024 * 1024), "total-limit artifact byte mismatch")
        run = await self.execute("artifact_duplicate", ["run_python"])
        await self.checked_execution(run)
        require(len(run["artifacts"]) == 2
                and len({a["filename"].split('/')[-1] for a in run["artifacts"]}) == 2,
                "same-basename artifacts overwritten/indistinguishable")
        contents = [await self.download(run, artifact) for artifact in run["artifacts"]]
        require(set(contents) == {b"\x01" * 128, b"\x02" * 128},
                "duplicate artifacts failed exact source-content checks")

    async def failure_categories(self) -> None:
        for category in ("timeout", "oom", "pids", "disk", "policy"):
            run = await self.execute(category, ["run_python"])
            execution = await self.checked_execution(run, category=category)
            self.answer(run, f"SANDBOX_{category.upper()}_OK")
            if category == "timeout":
                require(execution["duration_ms"] <= 5000, "2s timeout exceeded 3s termination tolerance")
            if category == "policy":
                records = (await self.request("GET", f"/runs/{run['id']}/executions/{execution['id']}/egress")).json()["items"]
                require(any(row["target_ip"] == "169.254.169.254" and row["action"] == "deny"
                            and row["port"] == 80 for row in records), "real policy-denial egress fact missing")
                require(bool(await self.audit("egress_denied", execution["id"])), "egress-denial audit missing")
                await self.request("GET", f"/runs/{run['id']}/executions/{execution['id']}/egress",
                                   identity="other", status=404)
        # A successful ordinary request after resource stress proves the API and
        # executor remain usable, rather than merely observing a killed process.
        await self.request("GET", "/stats/overview")
        await self.shell()

    async def egress_api(self) -> None:
        import uuid

        agent = (await self.request("POST", "/agents", status=201, json={
            "name": f"002-egress-api-{uuid.uuid4().hex[:8]}", "system_prompt": "fixture",
            "model": self.args.model, "tools": ["run_python"], "egress_mode": "restricted",
        })).json()
        path = f"/agents/{agent['id']}/egress-rules"
        await self.request("GET", path, identity="other", status=403)
        payload = {"target_type": "domain", "target": "pypi.org", "port_range": "443"}
        await self.request("POST", path, identity="other", status=403, json=payload)
        rule = (await self.request("POST", path, status=201, json=payload)).json()
        await self.request("POST", path, status=409, json=payload)
        await self.request("DELETE", path + f"/{rule['id']}", identity="other", status=403)
        await self.request("DELETE", path + f"/{rule['id']}", status=204)
        require(bool(await self.audit("egress_rule_delete", agent["id"])),
                "successful rule deletion was not audited")
        await self.request("POST", path, status=400,
                           json={"target_type": "ip", "target": "169.254.169.254"})
        await self.request("GET", "/admin/egress-blacklist", status=403)
        await self.request("POST", "/admin/egress-blacklist", status=403,
                           json={"target_type": "domain", "target": "example.com"})
        blacklist = (await self.request("GET", "/admin/egress-blacklist", identity="admin")).json()["items"]
        builtin = next(item for item in blacklist if item.get("builtin"))
        await self.request("DELETE", f"/admin/egress-blacklist/{builtin['id']}", status=403)
        await self.request("DELETE", f"/admin/egress-blacklist/{builtin['id']}", identity="admin", status=400)
        allowed_id = await self.create_run("egress_domain", ["run_python"],
                                           agent_options={"egress_mode": "restricted"}, egress_rules=[payload])
        allowed = await self.wait_run(allowed_id)
        execution = await self.checked_execution(allowed)
        self.answer(allowed, "SANDBOX_EGRESS_DOMAIN_OK")
        rows = (await self.request("GET", f"/runs/{allowed_id}/executions/{execution['id']}/egress")).json()["items"]
        require(any(row["target_domain"] == "pypi.org" and row["action"] == "allow"
                    and row["port"] == 443 and row.get("matched_rule_id") for row in rows),
                "API-configured domain allow did not reach native kernel/audit")
        denied_id = await self.create_run("egress_domain_denied", ["run_python"],
                                          agent_options={"egress_mode": "restricted"}, egress_rules=[payload])
        denied = await self.wait_run(denied_id)
        denied_execution = await self.checked_execution(denied, category="policy")
        require(bool(await self.audit("egress_denied", denied_execution["id"])),
                "restricted domain denial audit missing")
        global_rule = (await self.request("POST", "/admin/egress-blacklist", identity="admin", status=201,
                                         json={"target_type": "domain", "target": "example.com"})).json()
        try:
            run = await self.execute("egress_domain_denied", ["run_python"])
            execution = await self.checked_execution(run, category="policy")
            observed = (await self.request("GET", "/admin/egress-accesses", identity="admin",
                                           params={"run_id": run["id"], "action": "deny"})).json()
            require(observed["total"] > 0 and any(row.get("matched_rule_id") == global_rule["id"]
                                                for row in observed["items"]),
                    "global domain deny did not persist configured rule attribution")
        finally:
            await self.request("DELETE", f"/admin/egress-blacklist/{global_rule['id']}",
                               identity="admin", status=204)

    def ws_url(self, run_id: str, identity: str = "owner") -> str:
        parsed = urlsplit(self.base)
        return urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc,
                           parsed.path + f"/api/v1/ws/runs/{run_id}",
                           urlencode({"token": self.tokens[identity]}), ""))

    async def websocket(self) -> None:
        run_id = await self.create_run("ws", ["run_python"])
        events = []
        async with connect(self.ws_url(run_id), open_timeout=10, max_size=2 * 1024 * 1024) as ws:
            async with asyncio.timeout(self.args.timeout):
                for _ in range(4096):
                    event = json.loads(await ws.recv())
                    kind = event.get("type")
                    events.append(kind)
                    if kind == "sandbox_output":
                        require(len(event.get("chunk", "").encode()) <= 4096,
                                "WS output chunk exceeded 4096 UTF-8 bytes")
                    if kind == "sandbox_finished":
                        break
                else:
                    raise CheckFailure("WS event bound exceeded")
        require("ready" in events, "WS Redis subscription readiness missing")
        ordered = [kind for kind in events if kind in {"sandbox_started", "sandbox_output", "sandbox_finished"}]
        require(ordered and ordered[0] == "sandbox_started" and ordered[-1] == "sandbox_finished"
                and "sandbox_output" in ordered, "WS started/output/finished events absent/out of order")
        run = await self.wait_run(run_id)
        await self.checked_execution(run)
        try:
            async with connect(self.ws_url(run_id, "other"), open_timeout=5):
                raise CheckFailure("cross-owner WS subscription was accepted")
        except WebSocketException:
            pass  # Failed handshake is the required real ownership denial.
        self.metrics["websocket_event_types"] = events

    async def cancellation(self) -> None:
        run_id = await self.create_run("cancel", ["run_python"])
        async with connect(self.ws_url(run_id), open_timeout=10, max_size=2 * 1024 * 1024) as ws:
            async with asyncio.timeout(30):
                while True:
                    event = json.loads(await ws.recv())
                    if event.get("type") == "sandbox_output" and "cancel-started" in event.get("chunk", ""):
                        break
            started = time.monotonic()
            await self.request("POST", f"/runs/{run_id}/cancel")
            async with asyncio.timeout(10):
                while True:
                    event = json.loads(await ws.recv())
                    if event.get("type") == "sandbox_finished":
                        break
        require(time.monotonic() - started <= 5, "cancelled running script was not promptly terminated")
        run = await self.wait_run(run_id, expected_status="cancelled")
        await self.checked_execution(run, category="cancelled")

    async def infrastructure(self) -> None:
        run = await self.execute("infra", ["run_python"])
        execution = await self.checked_execution(run, category="infra")
        require(execution["exit_code"] is None and not (execution.get("stdout") or ""),
                "missing image failure fabricated output/exit code")
        self.answer(run, "SANDBOX_INFRA_OK")
        elapsed = (datetime.fromisoformat(execution["finished_at"])
                   - datetime.fromisoformat(execution["created_at"])).total_seconds()
        require(elapsed <= 15, "missing infrastructure did not fail quickly")

    async def quota(self) -> None:
        # Dedicated own worker has SBX_MAX_CONCURRENCY=1. Real 65s execution holds
        # the sole real Redis lease; others exhaust unchanged 5/15/25s backoff.
        runs = await asyncio.gather(*(self.execute("quota", ["run_python"]) for _ in range(5)))
        executions = []
        for run in runs:
            execution, = await self.executions(run, 1, "python")
            executions.append(execution)
            category = execution.get("error_category")
            await self.checked_execution(run, category=category)
        require(sum(e["status"] == "ok" for e in executions) == 1
                and sum(e.get("error_category") == "resource_busy" for e in executions) == 4,
                "five-way real quota saturation must yield one success/four readable busy results")
        self.metrics["quota"] = {"submitted": 5, "success": 1, "resource_busy": 4}

    async def performance(self) -> None:
        baseline, loaded = [], []
        failed = 0

        async def sample(destination: list[float]) -> None:
            nonlocal failed
            started = time.perf_counter()
            try:
                await self.request("GET", "/stats/overview")
            except Exception:
                failed += 1
            finally:
                # Every attempt is measured, including errors and timeouts.
                destination.append((time.perf_counter() - started) * 1000)

        for _ in range(100):
            await sample(baseline)
            await asyncio.sleep(.05)
        tasks = [asyncio.create_task(self.execute("sleep", ["run_python"])) for _ in range(20)]
        while not all(task.done() for task in tasks) or len(loaded) < 100:
            await sample(loaded)
            await asyncio.sleep(.05)
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        failures = sum(isinstance(result, BaseException) for result in outcomes)
        # Persist every latency before inspecting run payloads, so a bad/missing
        # execution cannot erase the failed load measurement from CI evidence.
        result = performance_verdict(baseline, loaded, failed)
        self.metrics["s6"] = result
        executions = []
        for run in outcomes:
            if isinstance(run, BaseException):
                continue
            try:
                execution, = await self.executions(run, 1, "python")
                require(execution["status"] == "ok" and execution["exit_code"] == 0
                        and execution["code"] == "import time\ntime.sleep(3)\nprint('sleep-completed')"
                        and (execution.get("stdout") or "").strip() == "sleep-completed",
                        "S6 sleep execution failed or its immutable three-second workload source differs")
                executions.append(execution)
            except Exception:
                failures += 1
        result.update({"submitted_runs": 20, "run_failures": failures,
                       "completed_executions": len(executions),
                       "execution_window_peak": interval_overlap(executions)})
        result["passed"] = result["passed"] and failures == 0 and len(executions) == 20
        require(result["passed"], "SC-004 failed: strict P95 ratio >1.2 or request/run failure (see sanitized measurements)")
        require(result["execution_window_peak"] >= 2, "S6 did not observe overlapping execution windows")
        overview = (await self.request("GET", "/stats/overview")).json()
        require(overview.get("sandbox_runs_today", 0) >= 20,
                "sandbox executions not counted in personal usage overview")


async def main(argv: list[str] | None = None) -> int:
    ap = parser()
    ap.description = __doc__
    ap.add_argument("--suite", choices=["functional", "infra", "quota", "performance"], default="functional")
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv)
    require(not args.skip_audit_check and not args.artifact_fetch_container,
            "full acceptance forbids audit skips and internal download workarounds")
    checks = {
        "functional": ["python_sum", "shell", "failure", "artifacts", "denied", "no_tools", "builtins",
                       "denial_threshold", "output_limits", "artifact_limits", "failure_categories", "egress_api", "websocket", "cancellation"],
        "infra": ["infrastructure"], "quota": ["quota"], "performance": ["performance"],
    }[args.suite]
    results = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False,
                                 limits=httpx.Limits(max_connections=64)) as http:
        verifier = FullVerifier(args, http)
        try:
            await verifier.setup()
        except Exception as exc:
            results.append({"name": "authenticated_setup", "passed": False, "error_type": type(exc).__name__})
        else:
            for name in checks:
                started = time.monotonic()
                try:
                    await getattr(verifier, name)()
                except Exception as exc:
                    result = {"name": name, "passed": False, "error_type": type(exc).__name__}
                    if isinstance(exc, CheckFailure):
                        result["assertion"] = str(exc)[:256]
                else:
                    result = {"name": name, "passed": True}
                result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                results.append(result)
                print(f"{'PASS' if result['passed'] else 'FAIL'} HTTP {name}", flush=True)
        document = {"suite": args.suite, "results": results,
                    "executions": verifier.evidence, "metrics": verifier.metrics, "run_ids": verifier.run_ids}
        Path(args.output).write_text(json.dumps(document, indent=2) + "\n")
    return 0 if len(results) == len(checks) and all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        print(f"FAIL HTTP harness: {type(exc).__name__} (details withheld)")
        raise SystemExit(1) from None
