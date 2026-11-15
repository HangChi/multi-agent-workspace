"""S5 real-kernel runtime checks on an isolated, locally owned HTTP target.

backend/.venv/bin/python deploy/verify/s5_egress_scenarios.py --output /tmp/002-egress.json
No Internet service is probed for IP/private policy tests. Benchmark-range and
private-range fixture containers are created here and removed in finally. Domain
rules are tested separately as a known fail-closed limitation, not a success.
Database/API rule ownership is covered by s3_sandbox_scenarios.py and API tests.
"""
from __future__ import annotations

import argparse
import json
import uuid

from sandbox_runtime import docker_client, probe, report


def connect_code(address: str, port: int = 8000) -> str:
    return (
        "import json,socket\n"
        "s=socket.socket(); s.settimeout(1)\n"
        f"rc=s.connect_ex(({address!r},{port})); s.close()\n"
        "print(json.dumps({'connected':rc==0,'errno':rc}))\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--public-url", help="Optional HTTPS DNS smoke target, e.g. https://pypi.org")
    args = parser.parse_args()
    client = docker_client()
    fixture_id = str(uuid.uuid4())
    labels = {"aw.verify": "002-egress", "aw.execution": fixture_id}
    networks, containers, results = [], [], []
    try:
        import docker

        from app.core.config import settings

        # Docker rejects overlapping subnets. Never adopt an existing network.
        addresses = []
        for subnet in ("198.18.254.0/24", "10.253.254.0/24"):
            prefix = subnet.rsplit(".", 1)[0]
            network = client.networks.create(
                f"aw-verify-{fixture_id}-{len(networks)}", driver="bridge", labels=labels,
                ipam=docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet=subnet)]),
            )
            networks.append(network)
            address = prefix + ".2"
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
            ready = server.exec_run(["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000',timeout=3).read()"])
            assert ready.exit_code == 0, ready.output.decode()
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
        if args.public_url:
            code = ("import urllib.request\n" + f"print(urllib.request.urlopen({args.public_url!r},timeout=5).status)")
            outcome = probe(client, code)
            dns_accesses = [r for r in outcome.get("egress", []) if r["port"] == 443 and r["domain"] != "ip-direct"]
            results.append({"name": "public HTTPS with DNS attribution", "passed": outcome.get("exit_code") == 0 and bool(dns_accesses) and not any(outcome["residual"].values()), "runtime": outcome})
        return report(results, args.output)
    finally:
        for container in reversed(containers):
            container.remove(force=True)
        for network in reversed(networks):
            network.remove()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
