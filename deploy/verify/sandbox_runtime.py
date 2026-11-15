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
        result.update(status="infra", error=f"{type(exc).__name__}: {exc}")
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
