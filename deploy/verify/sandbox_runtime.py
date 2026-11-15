"""Real Docker probes for 002; no fake SDKs and no application database access.

Run with backend/.venv/bin/python. These supplement (not replace) the HTTP
acceptance harness. Only resources labelled with each fresh execution ID are
checked/removed; unrelated containers, networks and volumes are never pruned.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


ERROR_STAGES = frozenset({
    'unknown', 'docker_connect', 'image_check', 'network_create', 'volume_create',
    'storage_start', 'storage_ready', 'policy_install', 'container_create',
    'script_inject', 'output_capture', 'container_start', 'container_wait', 'audit_collect',
    'fixture_config', 'fixture_network_create', 'fixture_container_create',
    'fixture_listener_ready', 'fixture_ipv6_create', 'fixture_probe',
    'fixture_cleanup', 'fixture_client_close',
})
ERROR_CODES = frozenset({
    'unknown', 'ipv6_pool_unavailable', 'address_pool_overlap', 'ipv6_unavailable',
    'permission_denied', 'address_in_use', 'address_unavailable', 'missing_executable',
    'invalid_argument', 'no_space_left', 'connection_refused', 'docker_api_error',
    'static_ip_requires_subnet',
})
ERROR_TYPES = frozenset({
    'SandboxInfraError', 'NetworkPolicyError', 'APIError', 'DockerException',
    'ImageNotFound', 'NotFound', 'ContainerError', 'ConnectionError', 'ReadTimeout',
    'Timeout', 'TimeoutError', 'OSError', 'PermissionError', 'FileNotFoundError',
    'ValueError', 'RuntimeError', 'ExecutionError', 'OtherError',
})


def failure_evidence(failure: BaseException | str) -> dict[str, Any]:
    """Classify privately; return only finite enums, class names and HTTP status."""
    chain, messages, statuses = [], [], []
    current: BaseException | None = failure if isinstance(failure, BaseException) else None
    visited: set[int] = set()
    while current is not None and id(current) not in visited and len(chain) < 8:
        visited.add(id(current))
        kind = type(current).__name__
        chain.append(kind if kind in ERROR_TYPES else 'OtherError')
        messages.append(str(current)[:8192])
        status = getattr(current, 'status_code', None)
        if isinstance(status, int) and 400 <= status <= 599:
            statuses.append(status)
        current = current.__cause__ or current.__context__
    if isinstance(failure, str):
        chain, messages = ['ExecutionError'], [failure[:8192]]
    text = '\n'.join(messages).lower()
    stage = next((value for marker, value in (
        ('无法连接 docker', 'docker_connect'), ('沙箱镜像', 'image_check'),
        ('创建沙箱网络失败', 'network_create'), ('创建有界沙箱卷', 'volume_create'),
        ('无法启动有界沙箱存储', 'storage_start'), ('有界沙箱存储未就绪', 'storage_ready'),
        ('安装沙箱出站策略失败', 'policy_install'), ('创建沙箱容器失败', 'container_create'),
        ('写入脚本失败', 'script_inject'), ('输出采集', 'output_capture'),
        ('启动沙箱容器失败', 'container_start'), ('等待沙箱容器失败', 'container_wait'),
        ('出站记录采集失败', 'audit_collect'),
    ) if marker in text), 'unknown')
    code = next((value for matched, value in (
        ('user specified ip address is supported only' in text, 'static_ip_requires_subnet'),
        ('ipv6' in text and 'pool' in text and 'available' in text, 'ipv6_pool_unavailable'),
        ('pool overlaps' in text, 'address_pool_overlap'),
        ('ipv6' in text and any(word in text for word in ('disabled', 'not enabled', 'not configured')), 'ipv6_unavailable'),
        (any(word in text for word in ('permission denied', 'operation not permitted')), 'permission_denied'),
        ('address already in use' in text, 'address_in_use'),
        ('cannot assign requested address' in text, 'address_unavailable'),
        ('executable file not found' in text, 'missing_executable'),
        ('invalid argument' in text, 'invalid_argument'),
        ('no space left on device' in text, 'no_space_left'),
        ('connection refused' in text, 'connection_refused'),
        (bool(statuses), 'docker_api_error'),
    ) if matched), 'unknown')
    result: dict[str, Any] = {
        'error_stage': stage, 'error_code': code, 'error_type': chain[0] if chain else 'OtherError',
        'error_chain': chain,
    }
    if statuses:
        result['daemon_status'] = statuses[-1]
    return result


def docker_client():
    import docker

    if not os.environ.get("DOCKER_HOST"):
        endpoint = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        os.environ["DOCKER_HOST"] = endpoint
    return docker.from_env()


def record_execution_id(execution_id: str) -> None:
    """Optional CI-only private UUID journal for cleanup after a killed probe."""
    path = os.environ.get("AW_VERIFY_ID_JOURNAL")
    if path:
        value = str(uuid.UUID(execution_id))
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(descriptor).st_size > 65536:
                raise ValueError("execution journal exceeded bounded CI budget")
            os.write(descriptor, (value + "\n").encode("ascii"))
        finally:
            os.close(descriptor)


def probe(client, code: str, *, snapshot=None, timeout_s=10, language="python", abort_check=None):
    from app.executors.sandbox import classify_outcome
    from app.executors.sandbox_client import SandboxRun

    eid = str(uuid.uuid4())
    record_execution_id(eid)
    started = time.monotonic()
    result: dict[str, Any] = {"execution_id": eid}
    try:
        with SandboxRun(
            execution_id=eid, language=language, code=code, client=client,
            timeout_s=timeout_s, egress_snapshot=snapshot, abort_check=abort_check,
        ) as run:
            outcome = run.execute()
            result.update(asdict(outcome))
            result["stdout"] = outcome.stdout.decode("utf-8", "replace")
            result["stderr"] = outcome.stderr.decode("utf-8", "replace")
            result["status"], result["error_category"] = classify_outcome(outcome)
            result["egress"] = run.egress_accesses
            result["policy_denied"] = run.policy_denied
            result["artifacts"] = [
                {"name": item.name, "size": item.size, "sha256": hashlib.sha256(item.data).hexdigest()}
                for item in run.iter_output_files()
            ]
    except Exception as exc:  # noqa: BLE001 - report probe failure after owned-resource cleanup
        result.update(status="infra", **failure_evidence(exc))
    finally:
        filters = {"label": f"aw.execution={eid}"}
        result["residual"] = {
            "containers": len(client.containers.list(all=True, filters=filters)),
            "networks": len(client.networks.list(filters=filters)),
            "volumes": len(client.volumes.list(filters=filters)),
        }
        result["total_ms"] = round((time.monotonic() - started) * 1000)
    return result


def report(results: list[dict[str, Any]], output: str | None) -> int:
    document = {"suite": Path(sys.argv[0]).stem, "results": results}
    for item in results:
        print(f"{'PASS' if item['passed'] else 'FAIL'} {item['name']}", flush=True)
    if output:
        Path(output).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    else:
        print(json.dumps(document, indent=2, ensure_ascii=False))
    return 0 if all(item["passed"] for item in results) else 1
