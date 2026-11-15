"""Native Ubuntu CI entrypoint for 002, never an existing local deployment.

Run on a fresh GitHub ubuntu-24.04 VM after installing backend + httpx/websockets:
  python deploy/verify/native_acceptance.py --output "$RUNNER_TEMP/002-results"

The program builds images, creates its OWN random-credential stack, bootstraps
only an empty CI database, runs real HTTP/native probes, and always removes its
compose project and execution-labelled resources. No deploy/.env is loaded.
Raw build/service/probe logs remain in private temporary files and are deleted;
only bounded allowlisted JSON is published. Only this run's resources are removed.
"""
# ruff: noqa: BLE001
# Sanitization boundaries also catch cancellation to guarantee own-stack cleanup.
from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import platform
import re
import secrets
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import docker
from sandbox_runtime import ERROR_CODES, ERROR_STAGES, ERROR_TYPES

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / "deploy" / "verify"
COMPOSE = ROOT / "deploy" / "docker-compose.verify.yml"


class AcceptanceFailure(RuntimeError):
    """Static diagnostics only; never include process output/exception strings."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def project_name(environ: dict[str, str]) -> str:
    run, attempt = environ.get("GITHUB_RUN_ID", ""), environ.get("GITHUB_RUN_ATTEMPT", "")
    require(bool(re.fullmatch(r"[0-9]+", run)) and bool(re.fullmatch(r"[0-9]+", attempt)),
            "native acceptance requires a GitHub run identity")
    return f"aw002ci-{run}-{attempt}"


def unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def sanitized_runtime(document: dict[str, Any]) -> dict[str, Any]:
    """Deliberate allowlist: stdout/errors/code/URLs/packets never leave tempdir."""
    results = document.get("results")
    require(isinstance(results, list) and 0 < len(results) <= 100,
            "runtime suite produced no valid bounded results")
    safe = []
    for item in results:
        require(isinstance(item, dict) and isinstance(item.get("passed"), bool),
                "runtime result lacks explicit pass/fail")
        result = {"name": str(item.get("name", "unnamed"))[:160],
                  "passed": item["passed"] and not item.get("skipped", False)
                  and item.get("status") not in {"skip", "skipped"}}
        runtime = item.get("runtime") or {}
        for key in ("execution_id", "status", "error_category", "exit_code", "duration_ms", "total_ms",
                    "cpu_ms", "mem_peak_bytes", "stdout_bytes", "stderr_bytes", "stdout_retained",
                    "stderr_retained", "streamed_bytes", "residual", "kernel_residual",
                    "error_stage", "error_code", "error_type", "error_chain", "daemon_status"):
            value = runtime.get(key)
            if value is not None:
                if key == "error_stage":
                    require(value in ERROR_STAGES, "runtime error stage outside finite allowlist")
                elif key == "error_code":
                    require(value in ERROR_CODES, "runtime error code outside finite allowlist")
                elif key == "error_type":
                    require(value in ERROR_TYPES, "runtime error type outside finite allowlist")
                elif key == "error_chain":
                    require(isinstance(value, list) and 0 < len(value) <= 8
                            and all(item in ERROR_TYPES for item in value),
                            "runtime error chain outside finite allowlist")
                elif key == "daemon_status":
                    require(type(value) is int and 400 <= value <= 599,
                            "runtime daemon status outside HTTP range")
                result[key] = value
        # No arbitrary per-script exception messages or output are copied.
        safe.append(result)
    return {"suite": str(document.get("suite", "runtime"))[:80], "results": safe}


def bridge_filtering_status(root: Path = Path('/proc/sys/net/bridge')) -> dict[str, int]:
    """Verify real per-family bridge hooks; a loaded module alone is insufficient."""
    values = {}
    for name in ('bridge-nf-call-iptables', 'bridge-nf-call-ip6tables'):
        path = root / name
        require(path.is_file() and path.read_text().strip() == '1',
                f'native bridge netfilter unavailable/disabled: {name}')
        values[name] = 1
    return values


class NativeRunner:
    def __init__(self, output: Path):
        require(platform.system() == "Linux" and "linuxkit" not in platform.release().lower(),
                "acceptance must execute on native Linux, not Docker Desktop")
        require(os.environ.get("GITHUB_ACTIONS") == "true", "this bootstrap is GitHub CI-owned only")
        self.project = project_name(dict(os.environ))
        self.prefix = self.project + "-sbx"
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.private = tempfile.TemporaryDirectory(prefix="aw002-private-")
        self.work = Path(self.private.name)
        self.env = dict(os.environ)
        # Do not reuse an inherited production/app credential. Every secret here
        # is generated for this disposable run and passed only in process env.
        self.env.update({
            "AW_VERIFY_PASSWORD": secrets.token_hex(24), "AW_VERIFY_JWT_SECRET": secrets.token_hex(48),
            "AW_VERIFY_INVITE_CODE": secrets.token_hex(24), "AW_VERIFY_USER_PASSWORD": secrets.token_urlsafe(32) + "Aa1!",
            "AW_VERIFY_CI": "1", "AW_VERIFY_RUN_ID": self.project,
            "AW_VERIFY_ID_JOURNAL": str(self.work / "execution-ids.txt"),
            "AW_VERIFY_DNS_NAME": self.project + "-dns", "AW_VERIFY_SBX_PREFIX": self.prefix,
            "DOCKER_SOCK_GID": str(Path('/var/run/docker.sock').stat().st_gid),
            "AW_VERIFY_SBX_MEM_MB": "192", "AW_VERIFY_SBX_DISK_MB": "96", "AW_VERIFY_SBX_PIDS": "32", "AW_VERIFY_SBX_CONCURRENCY": "4",
            "AW_VERIFY_WORKERS": "8", "AW_VERIFY_WORKER_MEMORY": "3g", "AW_VERIFY_PORT": str(unused_port()),
            "APP_ENV": "development", "LOG_LEVEL": "critical", "PYTHONPATH": str(ROOT / "backend"),
            "EGRESS_DNS_PROXY": self.project + "-dns", "SBX_NETWORK_PREFIX": self.prefix,
            "SBX_MEM_MB": "192", "SBX_DISK_MB": "96", "SBX_PIDS": "32", "SBX_MAX_CONCURRENCY": "4",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        for name in ("AW_VERIFY_PASSWORD", "AW_VERIFY_JWT_SECRET", "AW_VERIFY_INVITE_CODE", "AW_VERIFY_USER_PASSWORD"):
            print("::add-mask::" + self.env[name], flush=True)
        # Settings cannot load a developer .env: commands run at ROOT (which is
        # checked below), every app secret/DSN is explicitly overridden, and
        # compose also receives --env-file /dev/null.
        require(not (ROOT / ".env").exists(), "refusing a root .env in native CI checkout")
        self.env.update({"DATABASE_URL": "postgresql+asyncpg://verify:" + self.env["AW_VERIFY_PASSWORD"] + "@postgres:5432/aw_verify",
                         "REDIS_URL": "redis://redis:6379/0", "JWT_SECRET": self.env["AW_VERIFY_JWT_SECRET"],
                         "INVITE_CODE": self.env["AW_VERIFY_INVITE_CODE"], "MINIO_ACCESS_KEY": "verifyadmin",
                         "MINIO_SECRET_KEY": self.env["AW_VERIFY_PASSWORD"], "MINIO_BUCKET": "sandbox-verify",
                         "MINIO_SECURE": "false", "LLM_API_KEY": "mock-only", "DB_POOL": "null"})
        self.client = docker.from_env()
        self.results: list[dict[str, Any]] = []
        self.execution_ids: set[str] = set()
        self.started = time.monotonic()
        self.sequence = 0
        self.started_stack = False

    def command(self, argv: list[str], *, stdin: str | None = None, timeout: int = 300,
                allow_failure: bool = False) -> subprocess.CompletedProcess:
        # Captured output is neither printed nor included in artifacts. Even an
        # unexpected daemon/SQL error cannot leak a DSN or presigned query string.
        self.sequence += 1
        self.last_command = [argument[:256] for argument in argv[:32]]
        log_path = self.work / f"process-{self.sequence}.log"
        with log_path.open("wb") as log:
            completed = subprocess.run(argv, input=stdin.encode() if stdin is not None else None,
                                       stdout=log, stderr=subprocess.STDOUT, env=self.env,
                                       cwd=ROOT, timeout=timeout, check=False)
        require(log_path.stat().st_size <= 32 * 1024 * 1024, "process output exceeded private log ceiling")
        if not allow_failure:
            require(completed.returncode == 0, "command failed; raw output withheld to protect CI credentials")
        completed.stdout = log_path.read_bytes()
        return completed

    def compose(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess:
        return self.command(["docker", "compose", "--env-file", "/dev/null", "-p", self.project,
                             "-f", str(COMPOSE), *args], **kwargs)

    def fixture(self, phase: str) -> dict[str, Any]:
        result = self.compose("run", "--rm", "--no-deps", "-T", "migrate", "python", "-", phase,
                              stdin=(VERIFY / "ci_fixture.py").read_text(), allow_failure=True)
        try:
            payload = json.loads(result.stdout.decode().splitlines()[-1])
        except (ValueError, IndexError):
            raise AcceptanceFailure("CI fixture did not return sanitized JSON") from None
        require(result.returncode == 0 and payload.get("passed") is True,
                f"CI {phase} fixture failed (credentials withheld)")
        self.execution_ids.update(payload.get("execution_ids", []))
        return payload

    def wait_worker(self, service: str) -> None:
        # Readiness polling is outside S6's zero-retry measured HTTP attempts.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            reply = self.compose("exec", "-T", service, "celery", "-A", "app.tasks.celery_app:celery_app",
                                 "inspect", "ping", "--timeout", "2", allow_failure=True, timeout=15)
            if reply.returncode == 0 and b"pong" in reply.stdout:
                return
            time.sleep(1)
        raise AcceptanceFailure("own Celery worker did not become ready")

    def prepare(self) -> None:
        project_filter = {"label": f"com.docker.compose.project={self.project}"}
        existing = [*self.client.containers.list(all=True, filters=project_filter),
                    *self.client.networks.list(filters=project_filter), *self.client.volumes.list(filters=project_filter)]
        require(not existing, "refusing to reuse an existing verification compose project")
        daemon = self.client.version()
        daemon_info = self.client.info()
        require(daemon.get("Os") == "linux", "Docker daemon is not Linux")
        require(daemon_info.get("CgroupDriver") in {"systemd", "cgroupfs"},
                "Docker cgroup driver unavailable")
        bridge = self.client.networks.get("bridge")
        gateway = bridge.attrs["IPAM"]["Config"][0]["Gateway"]
        import ipaddress
        require(ipaddress.ip_address(gateway).version == 4, "native Docker bridge IPv4 gateway unavailable")
        minio_port = unused_port()
        self.env.update({"AW_VERIFY_MINIO_BIND": gateway, "AW_VERIFY_MINIO_PORT": str(minio_port),
                         "AW_VERIFY_MINIO_ENDPOINT": f"{gateway}:{minio_port}"})
        self.results.append({"name": "native_environment", "passed": True,
                             "kernel": platform.release(), "docker_version": daemon.get("Version"),
                             "uname": list(platform.uname()),
                             "cgroup_mode": "v2" if Path('/sys/fs/cgroup/cgroup.controllers').exists() else "v1",
                             "docker_info": {key: daemon_info.get(key) for key in
                                             ("CgroupDriver", "CgroupVersion", "Driver", "NCPU", "MemTotal", "SecurityOptions", "KernelVersion", "OperatingSystem")},
                             "architecture": daemon.get("Arch"), "runner_os": os.environ.get("RUNNER_OS"),
                             "git_sha": os.environ.get("GITHUB_SHA"), "project": self.project})
        for name, dockerfile, context, tags in [
            ("sandbox", "deploy/sandbox/Dockerfile", "deploy/sandbox", ["aw-sandbox-python:3.12"]),
            ("net_helper", "deploy/sbx-net/Dockerfile", "deploy/sbx-net", ["aw-sbx-net:1"]),
            ("dns", "deploy/dns-proxy/Dockerfile", "deploy/dns-proxy", ["aw-dns-proxy:1"]),
            ("minio", "deploy/verify/minio.Dockerfile", "deploy/verify", ["aw-verify-minio:2025-09-07"]),
            ("backend", "backend/Dockerfile", "backend", ["aw-api:latest", "aw-worker:latest", "aw-migrate:latest"]),
            ("frontend", "frontend/Dockerfile", "frontend", ["aw-frontend:latest"]),
        ]:
            args = ["docker", "build", "-f", str(ROOT / dockerfile)]
            for tag in tags:
                args.extend(["-t", tag])
            self.command([*args, str(ROOT / context)], timeout=1200)
            self.results.append({"name": f"build_{name}", "passed": True})
            print(f"PASS build {name}", flush=True)
        versions = {}
        for executable in ("iptables", "ip6tables", "nft", "conntrack", "tcpdump"):
            output = self.client.containers.run("aw-sbx-net:1", command=[executable, "--version"], remove=True,
                                                network_mode="host", cap_add=["NET_ADMIN"],
                                                labels={"aw.verify": self.project}, mem_limit="64m", pids_limit=16)
            versions[executable] = output.decode("utf-8", "replace").splitlines()[0][:160]
        bridge_filters = bridge_filtering_status()
        self.results.append({"name": "native_netfilter_preflight", "passed": True,
                             "versions": versions, "bridge_filters": bridge_filters})
        self.started_stack = True
        self.compose("up", "-d", "--wait", "--wait-timeout", "120", "postgres", "redis", "minio", "dns-proxy", "llm-gateway")
        # Exercise the real complete Alembic chain with legacy data in between,
        # not a reconstructed test schema or unit-only mapping approximation.
        self.compose("run", "--rm", "--no-deps", "migrate", "alembic", "upgrade", "7c1e9a4b2d30")
        self.fixture("seed")
        self.compose("run", "--rm", "--no-deps", "migrate", "alembic", "upgrade", "head")
        self.results.append({"name": "migration_and_history", **self.fixture("verify")})
        self.compose("up", "-d", "--wait", "--wait-timeout", "120", "frontend", "worker")
        # API readiness does not imply Celery is consuming. Wait for an actual
        # worker control reply; missing worker/dependency is a failure, not skip.
        self.wait_worker("worker")

    def run_suite(self, name: str, argv: list[str], *, runtime: bool = False, timeout: int = 600) -> None:
        raw = self.work / f"{name}.json"
        started = time.monotonic()
        result = self.command([sys.executable, *argv, "--output", str(raw)], allow_failure=True, timeout=timeout)
        require(raw.is_file() and raw.stat().st_size <= 4 * 1024 * 1024,
                f"{name} did not produce bounded result evidence")
        document = json.loads(raw.read_text())
        safe = sanitized_runtime(document) if runtime else document
        for item in document.get("results", []):
            eid = (item.get("runtime") or {}).get("execution_id")
            if eid:
                self.execution_ids.add(eid)
        self.execution_ids.update(e["execution_id"] for e in document.get("executions", []))
        self.execution_ids.update(document.get("execution_ids", []))
        cases = safe.get("results", [])
        required = {"http_functional": 14, "http_infra": 1, "http_quota": 1, "http_performance": 1,
                    "native_io": 2, "native_escape_matrix": 13, "native_egress_ip_ipv6": 12,
                    "native_egress_domains": 18}[name]
        passed = (result.returncode == 0 and len(cases) >= required
                  and all(item.get("passed") is True and not item.get("skipped")
                          and item.get("status") not in {"skip", "skipped"} for item in cases))
        self.results.append({"name": name, "passed": passed, "cases": len(cases), "minimum_required_cases": required,
                             "failed_cases": sum(item.get("passed") is not True for item in cases),
                             "elapsed_ms": round((time.monotonic() - started) * 1000)})
        self.write_json(name + ".json", safe)
        print(f"{'PASS' if passed else 'FAIL'} suite {name}", flush=True)

    def http_suite(self, suite: str) -> None:
        self.run_suite("http_" + suite, [str(VERIFY / "full_stack_acceptance.py"),
                       "--base", f"http://127.0.0.1:{self.env['AW_VERIFY_PORT']}",
                       "--suite", suite, "--timeout", "240", "--poll-interval", "0.5"], timeout=900)

    def performance(self) -> None:
        observed: list[int] = []
        errors: list[str] = []
        stop = threading.Event()

        def observe() -> None:
            while not stop.is_set():
                try:
                    containers = self.client.containers.list(filters={"label": "aw.managed=true", "status": "running"})
                    count = sum(c.name.startswith(self.prefix + "-") and c.name.endswith("-c") for c in containers)
                    observed.append(count)
                except Exception as exc:
                    errors.append(type(exc).__name__)
                stop.wait(.1)

        watcher = threading.Thread(target=observe, daemon=True)
        watcher.start()
        try:
            # Same observer runs across BOTH baseline and loaded phases.
            self.http_suite("performance")
        finally:
            stop.set()
            watcher.join(timeout=5)
        peak = max(observed, default=0)
        passed = not errors and not watcher.is_alive() and 2 <= peak <= 4
        self.results.append({"name": "s6_live_concurrency", "passed": passed, "peak_live_sandboxes": peak,
                             "configured_limit": 4, "samples": len(observed), "observer_failures": len(errors)})
        self.write_json("s6-concurrency.json", {"peak": peak, "limit": 4, "samples": observed,
                                               "observer_error_types": errors, "passed": passed})

    def helper(self, command: list[str]) -> bytes:
        return self.client.containers.run("aw-sbx-net:1", command=command, remove=True,
                                          network_mode="host", cap_add=["NET_ADMIN"],
                                          labels={"aw.verify_run": self.project}, mem_limit="64m", pids_limit=16)

    def owned_fixture_resources(self) -> dict[str, list[Any]]:
        filters = {"label": f"aw.verify_run={self.project}"}
        return {"containers": self.client.containers.list(all=True, filters=filters),
                "networks": self.client.networks.list(filters=filters),
                "volumes": self.client.volumes.list(filters=filters)}

    def discover_owned_execution_ids(self) -> None:
        journal = Path(self.env["AW_VERIFY_ID_JOURNAL"])
        if journal.exists():
            require(journal.stat().st_size <= 65536, "own execution UUID journal exceeded ceiling")
            self.execution_ids.update(str(uuid.UUID(line)) for line in journal.read_text().splitlines())
        for kind, collection in (("containers", self.client.containers),
                                 ("networks", self.client.networks), ("volumes", self.client.volumes)):
            objects = collection.list(all=True) if kind == "containers" else collection.list()
            for item in objects:
                labels = item.labels if hasattr(item, "labels") else item.attrs.get("Labels", {})
                eid = (labels or {}).get("aw.execution")
                if (item.name.startswith(self.prefix + "-")
                        or (labels or {}).get("aw.verify_run") == self.project) and eid:
                    self.execution_ids.add(str(uuid.UUID(eid)))

    def kernel_names(self) -> tuple[set[str], set[str]]:
        from app.executors.egress_network import _chain_names

        chains = {name + suffix for eid in self.execution_ids for name in _chain_names(eid) for suffix in ("", "6")}
        tables = {prefix + uuid.UUID(eid).hex for eid in self.execution_ids for prefix in ("awd_", "awp_")}
        return chains, tables

    def kernel_residual(self) -> dict[str, int]:
        chains, tables = self.kernel_names()
        counts = {}
        for command in ("iptables-save", "ip6tables-save"):
            tokens = re.findall(r"[A-Za-z0-9_-]+", self.helper([command]).decode("utf-8", "replace"))
            counts[command] = len(chains.intersection(tokens))
        listed = json.loads(self.helper(["nft", "-j", "list", "tables"]))
        counts["nft_tables"] = sum(item.get("table", {}).get("family") == "inet"
                                   and item.get("table", {}).get("name") in tables
                                   for item in listed.get("nftables", []))
        return counts

    def cleanup_kernel(self) -> None:
        # Remove only exact, UUID-derived own chains/tables, never host/global
        # policies. Parsing identifies references but can never broaden scope.
        chains, tables = self.kernel_names()
        commands = []
        for executable, save in (("iptables", "iptables-save"), ("ip6tables", "ip6tables-save")):
            text = self.helper([save]).decode("utf-8", "replace")
            present = chains.intersection(re.findall(r"[A-Za-z0-9_-]+", text))
            for line in text.splitlines():
                parts = shlex.split(line)
                if (len(parts) >= 4 and parts[0] == "-A" and parts[1] in {"FORWARD", "INPUT"}
                        and "-j" in parts and parts[parts.index("-j") + 1] in present):
                    commands.append([executable, "-D", *parts[1:]])
            for chain in sorted(present):
                commands.extend([[executable, "-F", chain], [executable, "-X", chain]])
        listed = json.loads(self.helper(["nft", "-j", "list", "tables"]))
        for item in listed.get("nftables", []):
            table = item.get("table", {})
            if table.get("family") == "inet" and table.get("name") in tables:
                commands.append(["nft", "delete", "table", "inet", table["name"]])
        for offset in range(0, len(commands), 32):
            encoded = base64.b64encode(json.dumps({"commands": commands[offset:offset + 32],
                                                  "best_effort": True}).encode()).decode()
            self.helper(["python3", "/opt/aw/command_batch.py", encoded])
        require(not any(self.kernel_residual().values()), "own kernel rules remain after bounded cleanup")

    def residual(self) -> dict[str, int]:
        counts = {"containers": 0, "networks": 0, "volumes": 0}
        for eid in self.execution_ids:
            filters = {"label": f"aw.execution={eid}"}
            counts["containers"] += len(self.client.containers.list(all=True, filters=filters))
            counts["networks"] += len(self.client.networks.list(filters=filters))
            counts["volumes"] += len(self.client.volumes.list(filters=filters))
        counts.update({"fixture_" + kind: len(items) for kind, items in self.owned_fixture_resources().items()})
        return counts

    def write_json(self, name: str, content: dict[str, Any]) -> None:
        data = json.dumps(content, indent=2).encode() + b"\n"
        require(len(data) <= 1024 * 1024, "sanitized report exceeds 1MiB hard ceiling")
        # Defense in depth in addition to schema allowlists; mask directives alone
        # do NOT redact uploaded files. Reject the file rather than publishing it.
        for key, secret in self.env.items():
            if key in {"AW_VERIFY_PASSWORD", "AW_VERIFY_JWT_SECRET", "AW_VERIFY_INVITE_CODE", "AW_VERIFY_USER_PASSWORD"}:
                require(secret.encode() not in data, "credential detected in sanitized evidence")
        require(b"Bearer " not in data and b"X-Amz-Signature" not in data,
                "authorization material detected in sanitized evidence")
        target = self.output / name
        target.write_bytes(data)
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def cleanup(self) -> None:
        if not self.started_stack:
            # A rejected pre-existing project is NEVER adopted by cleanup.
            self.client.close()
            self.private.cleanup()
            return
        errors = []

        def attempt(operation) -> None:
            try:
                operation()
            except Exception as exc:
                errors.append(type(exc).__name__)

        def remove_network(network) -> None:
            for container in self.client.containers.list(filters={"network": network.name}):
                labels = container.labels or {}
                require(labels.get("com.docker.compose.project") == self.project
                        or labels.get("aw.verify_run") == self.project
                        or labels.get("aw.execution") in self.execution_ids,
                        "refusing to disconnect an unknown network attachment")
                network.disconnect(container, force=True)
            network.remove()

        try:
            # Stop only this project's consumers before gathering still-running
            # own IDs. A failure in one cleanup never skips the remaining work.
            attempt(lambda: self.compose("--profile", "faults", "stop", "--timeout", "15", "worker", "worker-infra", "worker-quota",
                                         allow_failure=True, timeout=90))
            with contextlib.suppress(Exception):
                self.fixture("facts")
            attempt(self.discover_owned_execution_ids)
            for eid in self.execution_ids:
                filters = {"label": f"aw.execution={eid}"}
                for item in self.client.containers.list(all=True, filters=filters):
                    attempt(lambda item=item: item.remove(force=True, v=True))
                for item in self.client.volumes.list(filters=filters):
                    attempt(lambda item=item: item.remove(force=True))
                for item in self.client.networks.list(filters=filters):
                    attempt(lambda item=item: remove_network(item))
            # Standalone S5 targets/DNS fixtures have an exact project marker,
            # not necessarily an aw.execution label. Never infer ownership by
            # aw-verify-* names or delete another run's similarly named objects.
            fixtures = self.owned_fixture_resources()
            for item in fixtures["containers"]:
                attempt(lambda item=item: item.remove(force=True, v=True))
            for item in fixtures["volumes"]:
                attempt(lambda item=item: item.remove(force=True))
            for item in fixtures["networks"]:
                attempt(lambda item=item: remove_network(item))
            attempt(self.cleanup_kernel)
            attempt(lambda: require(not any(self.residual().values()), "own execution/fixture resources remain after cleanup"))
        finally:
            attempt(lambda: self.compose("--profile", "faults", "down", "--volumes", "--remove-orphans", "--timeout", "15", timeout=120))
            self.client.close()
            self.private.cleanup()
        require(not errors, "one or more own-resource cleanup operations failed")

    def run(self) -> int:
        try:
            self.prepare()
            self.http_suite("functional")
            for name, script, extras in [
                ("native_io", "s2_io_scenarios.py", []),
                ("native_escape_matrix", "sbx_escape_matrix.py", []),
                ("native_egress_ip_ipv6", "s5_egress_scenarios.py", ["--public-url", "https://pypi.org"]),
                ("native_egress_domains", "s5_domain_runtime.py", []),
            ]:
                self.run_suite(name, [str(VERIFY / script), *extras], runtime=True, timeout=600)
            self.performance()
            self.compose("stop", "--timeout", "20", "worker")
            for service, suite in (("worker-infra", "infra"), ("worker-quota", "quota")):
                self.compose("--profile", "faults", "up", "-d", "--no-deps", service)
                self.wait_worker(service)
                self.http_suite(suite)
                self.compose("stop", "--timeout", "20", service)
            facts = self.fixture("facts")
            self.results.append({"name": "db_persistence_audit_invariants", **facts})
            self.discover_owned_execution_ids()
            residual, kernel = self.residual(), self.kernel_residual()
            self.results.append({"name": "execution_residuals", "passed": not any([*residual.values(), *kernel.values()]),
                                 "resources": residual, "kernel_chains": kernel})
        except BaseException as exc:
            failure = {"name": "native_runner", "passed": False, "error_type": type(exc).__name__,
                       "command": getattr(self, "last_command", [])}
            if isinstance(exc, AcceptanceFailure):
                failure["assertion"] = str(exc)[:256]
            self.results.append(failure)
            print(f"FAIL native runner: {type(exc).__name__} (details withheld)", flush=True)
        finally:
            try:
                self.cleanup()
                self.results.append({"name": "own_project_cleanup", "passed": True})
            except BaseException as exc:
                self.results.append({"name": "own_project_cleanup", "passed": False, "error_type": type(exc).__name__})
            passed = bool(self.results) and all(item.get("passed") is True for item in self.results)
            self.write_json("summary.json", {"suite": "002-native-linux", "passed": passed,
                                             "elapsed_ms": round((time.monotonic() - self.started) * 1000),
                                             "results": self.results})
        return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    # A SIGTERM/cancel still takes the exact same own-project finally cleanup.
    def interrupted(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt("CI cancellation")
    signal.signal(signal.SIGTERM, interrupted)
    return NativeRunner(args.output).run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL native bootstrap: {type(exc).__name__} (details withheld)")
        raise SystemExit(1) from None
