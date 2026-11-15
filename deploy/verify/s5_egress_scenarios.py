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
import os
import uuid

from sandbox_runtime import docker_client, probe, report


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
    client = docker_client()
    fixture_id = str(uuid.uuid4())
    labels = {"aw.verify": "002-egress", "aw.execution": fixture_id}
    if os.environ.get("AW_VERIFY_RUN_ID"):
        labels["aw.verify_run"] = os.environ["AW_VERIFY_RUN_ID"]
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
        for name, snapshot, connected in [
            ("same target three allowed TCP connections", None, True),
            ("same target three denied TCP connections", {"mode": "restricted"}, False),
        ]:
            code = (
                "import json,socket\nresults=[]\n"
                "for port in [41001,41002,41003]:\n"
                " s=socket.socket(); s.settimeout(0.25); s.bind(('',port))\n"
                f" results.append(s.connect_ex(({public_ip!r},8000))==0); s.close()\n"
                "print(json.dumps(results))\n"
            )
            outcome = probe(client, code, snapshot=snapshot)
            access = [r for r in outcome.get("egress", []) if r["target_ip"] == public_ip and r["port"] == 8000]
            try:
                observed = json.loads(outcome.get("stdout", ""))
            except ValueError:
                observed = None
            passed = (
                outcome.get("exit_code") == 0 and observed == [connected] * 3
                and len(access) == 3
                and {r.get("source_port") for r in access} == {41001, 41002, 41003}
                and all(r["action"] == ("allow" if connected else "deny") for r in access)
                and not any(outcome["residual"].values())
            )
            results.append({"name": name, "passed": passed, "runtime": outcome})
        # Documentation-prefix IPv6 listener on another owned bridge; no public
        # IPv6 service is probed. The sandbox's *actual* reserved IPv6 source must
        # appear in the audit, not merely a successful connection result.
        segment = fixture_id.replace("-", "")[:4]
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
        ready = ipv6_server.exec_run([
            "python", "-c",
            (
                "import socket,time\n"
                "for _ in range(100):\n"
                " try: socket.create_connection(('::1',8000),timeout=0.1).close(); break\n"
                " except OSError: time.sleep(0.02)\n"
                "else: raise RuntimeError('IPv6 fixture listener did not start')\n"
            ),
        ])
        assert ready.exit_code == 0, ready.output.decode()
        v6_allow = {"id": str(uuid.uuid4()), "action": "allow", "target_type": "ip", "target": ipv6_target, "port_range": "8000"}
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
        return report(results, args.output)
    finally:
        for container in reversed(containers):
            container.remove(force=True)
        for network in reversed(networks):
            network.remove()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
