"""S5 real-kernel runtime checks on an isolated, locally owned HTTP target.

backend/.venv/bin/python deploy/verify/s5_egress_scenarios.py --output /tmp/002-egress.json
No Internet service is probed for IP/private policy tests. Benchmark-range and
private-range fixture containers are created here and removed in finally. Domain
mediation is covered by the separate s5_domain_runtime.py matrix.
Database/API rule ownership is covered by s3_sandbox_scenarios.py and API tests.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any

from sandbox_runtime import docker_client, failure_evidence, probe, report

_FIXTURE_STAGES = frozenset({
    "docker_connect", "fixture_config", "fixture_network_create", "fixture_container_create",
    "fixture_listener_ready", "fixture_ipv6_create", "fixture_probe",
    "fixture_cleanup", "fixture_client_close",
})


def _failure_result(name: str, failure: BaseException | str, stage: str) -> dict[str, Any]:
    """Build bounded evidence without retaining daemon exception text."""
    facts = failure_evidence(failure)
    facts["error_stage"] = stage if stage in _FIXTURE_STAGES else "fixture_probe"
    return {"name": name, "passed": False, "runtime": {"status": "infra", **facts}}


def _wait_for_listener(server: Any, host: str, *, attempts: int = 100) -> None:
    """Poll an owned listener with a finite in-container readiness budget."""
    if not 1 <= attempts <= 100:
        raise ValueError("unsupported fixture readiness budget")
    if host == "::1":
        family = "socket.AF_INET6"
    elif host == "127.0.0.1":
        family = "socket.AF_INET"
    else:
        raise ValueError("unsupported fixture readiness address")
    code = (
        "import socket,time\n"
        f"for _ in range({attempts}):\n"
        " try:\n"
        f"  with socket.socket({family}) as s: s.settimeout(0.1); s.connect(({host!r},8000))\n"
        "  break\n"
        " except OSError: time.sleep(0.02)\n"
        "else: raise RuntimeError('fixture listener did not start')\n"
    )
    ready = server.exec_run(["python", "-c", code])
    if ready.exit_code != 0:
        raise RuntimeError("fixture listener readiness check failed")


def _cleanup_owned(containers: list[Any], networks: list[Any], client: Any) -> list[dict[str, Any]]:
    """Remove only handles created by this invocation, continuing after errors."""
    failures: list[dict[str, Any]] = []
    for container in reversed(containers):
        try:
            container.remove(force=True)
        except Exception as exc:  # noqa: BLE001 - retain only bounded enum evidence
            failures.append(_failure_result("fixture container cleanup", exc, "fixture_cleanup"))
    for network in reversed(networks):
        try:
            network.remove()
        except Exception as exc:  # noqa: BLE001 - retain only bounded enum evidence
            failures.append(_failure_result("fixture network cleanup", exc, "fixture_cleanup"))
    if client is not None:
        try:
            client.close()
        except Exception as exc:  # noqa: BLE001 - retain only bounded enum evidence
            failures.append(_failure_result("fixture client cleanup", exc, "fixture_client_close"))
    return failures


def connect_code(address: str, port: int = 8000) -> str:
    return (
        "import json,socket\n"
        f"s=socket.socket({'socket.AF_INET6' if ':' in address else 'socket.AF_INET'}); s.settimeout({3 if ':' in address else 1})\n"
        f"rc=s.connect_ex(({address!r},{port})); s.close()\n"
        "print(json.dumps({'connected':rc==0,'errno':rc}))\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--public-url", help="Optional HTTPS DNS smoke target, e.g. https://pypi.org")
    args = parser.parse_args()
    client = None
    fixture_id = str(uuid.uuid4())
    labels = {"aw.verify": "002-egress", "aw.execution": fixture_id}
    if os.environ.get("AW_VERIFY_RUN_ID"):
        labels["aw.verify_run"] = os.environ["AW_VERIFY_RUN_ID"]
    networks, containers, results = [], [], []
    stage = "docker_connect"
    try:
        client = docker_client()
        stage = "fixture_config"
        import docker
        from app.core.config import settings

        # Docker rejects overlapping subnets. Never adopt an existing network.
        addresses = []
        for subnet in ("198.18.254.0/24", "10.253.254.0/24"):
            prefix = subnet.rsplit(".", 1)[0]
            stage = "fixture_network_create"
            network = client.networks.create(
                f"aw-verify-{fixture_id}-{len(networks)}", driver="bridge", labels=labels,
                ipam=docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet=subnet)]),
            )
            networks.append(network)
            address = prefix + ".2"
            stage = "fixture_container_create"
            server = client.containers.run(
                settings.sbx_image, command=["python", "-m", "http.server", "8000"],
                network=network.name, detach=True, labels=labels, read_only=True,
                cap_drop=["ALL"], security_opt=["no-new-privileges"],
                mem_limit="64m", pids_limit=16,
            )
            containers.append(server)
            server.reload()
            actual = server.attrs["NetworkSettings"]["Networks"][network.name]["IPAddress"]
            assert actual == address, (actual, address)
            # Confirm the controlled listener itself is healthy before policy checks.
            stage = "fixture_listener_ready"
            _wait_for_listener(server, "127.0.0.1")
            addresses.append(address)
        public_ip, private_ip = addresses
        allow = {"id": str(uuid.uuid4()), "action": "allow", "target_type": "ip", "target": public_ip, "port_range": "8000"}
        cases = [
            ("default direct IP allow", public_ip, 8000, None, True),
            ("builtin private deny", private_ip, 8000, None, False),
            ("metadata TCP deny (no HTTP payload)", "169.254.169.254", 80, None, False),
            ("restricted explicit IP allow", public_ip, 8000, {"mode": "restricted", "agent_rules": [allow]}, True),
            ("restricted empty deny", public_ip, 8000, {"mode": "restricted"}, False),
            ("global deny wins over agent allow", public_ip, 8000, {"mode": "restricted", "agent_rules": [allow], "global_rules": [{**allow, "id": str(uuid.uuid4()), "action": "deny"}]}, False),
        ]
        stage = "fixture_probe"
        for name, address, port, snapshot, connected in cases:
            outcome = probe(client, connect_code(address, port), snapshot=snapshot)
            try:
                observed = json.loads(outcome.get("stdout", ""))["connected"]
            except (ValueError, KeyError):
                observed = None
            access = [r for r in outcome.get("egress", []) if r["target_ip"] == address and r["port"] == port]
            expected_action = "allow" if connected else "deny"
            passed = (
                outcome.get("exit_code") == 0 and observed is connected
                and any(r["action"] == expected_action for r in access)
                and not any(outcome["residual"].values())
            )
            results.append({"name": name, "passed": passed, "runtime": outcome})
        for name, snapshot, connected in [
            ("same target three allowed TCP connections", None, True),
            ("same target three denied TCP connections", {"mode": "restricted"}, False),
        ]:
            # Three distinct concurrent source ports, requested from the kernel
            # rather than pinned to fixed numbers. The previous version bound
            # 41001/41002/41003 and asserted that exact set, which fails with
            # EADDRINUSE (and an unhelpful `exit_code: 1`) whenever any of those
            # ports is still in TIME_WAIT from an earlier execution on the same
            # host. The contract under test is "three separate connections from
            # three separate source ports", not those particular numbers, so the
            # kernel picks fresh ephemeral ports and both the socket options and
            # the ports actually used are reported back for the assertion.
            code = (
                "import json,socket\n"
                "opened=[]\n"
                "try:\n"
                " for _ in range(3):\n"
                "  s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
                "  s.settimeout(0.25); s.bind(('',0)); opened.append(s)\n"
                " ports=[s.getsockname()[1] for s in opened]\n"
                f" results=[s.connect_ex(({public_ip!r},8000))==0 for s in opened]\n"
                "finally:\n"
                " for s in opened: s.close()\n"
                "print(json.dumps({'ports':ports,'results':results}))\n"
            )
            outcome = probe(client, code, snapshot=snapshot)
            access = [r for r in outcome.get("egress", []) if r["target_ip"] == public_ip and r["port"] == 8000]
            try:
                observed = json.loads(outcome.get("stdout", ""))
            except ValueError:
                observed = None
            used_ports = (observed or {}).get("ports") if isinstance(observed, dict) else None
            used_results = (observed or {}).get("results") if isinstance(observed, dict) else None
            passed = (
                outcome.get("exit_code") == 0
                and used_results == [connected] * 3
                # Three *distinct* source ports, and the audit must name exactly
                # those, so a single reused port cannot be mistaken for three.
                and isinstance(used_ports, list) and len(set(used_ports)) == 3
                and len(access) == 3
                and {r.get("source_port") for r in access} == set(used_ports or [])
                and all(r["action"] == ("allow" if connected else "deny") for r in access)
                and not any(outcome["residual"].values())
            )
            results.append({"name": name, "passed": passed, "runtime": outcome})
        # Documentation-prefix IPv6 listener on another owned bridge; no public
        # IPv6 service is probed. The sandbox's *actual* reserved IPv6 source must
        # appear in the audit, not merely a successful connection result.
        segment = fixture_id.replace("-", "")[:4]
        stage = "fixture_ipv6_create"
        ipv6_network = client.networks.create(
            f"aw-verify-{fixture_id}-ipv6", driver="bridge", enable_ipv6=True, labels=labels,
            ipam=docker.types.IPAMConfig(pool_configs=[
                docker.types.IPAMPool(subnet="198.18.253.0/24"),
                docker.types.IPAMPool(subnet=f"2001:db8:{segment}::/64"),
            ]),
        )
        networks.append(ipv6_network)
        ipv6_server = client.containers.run(
            settings.sbx_image, command=["python", "-m", "http.server", "8000", "--bind", "::"],
            network=ipv6_network.name, detach=True, labels=labels, read_only=True,
            cap_drop=["ALL"], security_opt=["no-new-privileges"], mem_limit="64m", pids_limit=16,
        )
        containers.append(ipv6_server)
        ipv6_server.reload()
        ipv6_target = ipv6_server.attrs["NetworkSettings"]["Networks"][ipv6_network.name]["GlobalIPv6Address"]
        stage = "fixture_listener_ready"
        _wait_for_listener(ipv6_server, "::1")
        v6_allow = {"id": str(uuid.uuid4()), "action": "allow", "target_type": "ip", "target": ipv6_target, "port_range": "8000"}
        stage = "fixture_probe"
        for name, snapshot, connected in [
            ("actual source IPv6 default allow audit", None, True),
            ("actual source IPv6 restricted deny audit", {"mode": "restricted"}, False),
            ("actual source IPv6 global deny precedence", {"mode": "restricted", "agent_rules": [v6_allow], "global_rules": [{**v6_allow, "action": "deny"}]}, False),
        ]:
            outcome = probe(client, connect_code(ipv6_target), snapshot=snapshot)
            access = [r for r in outcome.get("egress", []) if r["target_ip"] == ipv6_target and r["port"] == 8000]
            try:
                observed = json.loads(outcome.get("stdout", ""))["connected"]
            except (ValueError, KeyError):
                observed = None
            passed = (
                outcome.get("exit_code") == 0 and observed is connected and len(access) == 1
                and ":" in access[0].get("source_ip", "")
                and access[0]["action"] == ("allow" if connected else "deny")
                and not any(outcome["residual"].values())
            )
            results.append({"name": name, "passed": passed, "runtime": outcome})
        if args.public_url:
            code = ("import urllib.request\n" + f"print(urllib.request.urlopen({args.public_url!r},timeout=5).status)")
            outcome = probe(client, code)
            dns_accesses = [r for r in outcome.get("egress", []) if r["port"] == 443 and r["domain"] != "ip-direct"]
            results.append({"name": "public HTTPS with DNS attribution", "passed": outcome.get("exit_code") == 0 and bool(dns_accesses) and not any(outcome["residual"].values()), "runtime": outcome})
    except Exception as exc:  # noqa: BLE001 - bounded setup evidence is required
        results.append(_failure_result("fixture setup", exc, stage))
    finally:
        results.extend(_cleanup_owned(containers, networks, client))
    return report(results, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
