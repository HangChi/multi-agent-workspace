#!/usr/bin/env python3
"""Owned, deterministic T060 Linux-kernel probe; no DB, .env, public DNS or TLS.

Run from repository root: PYTHONPATH=backend backend/.venv/bin/python deploy/verify/s5_domain_runtime.py
Build aw-sbx-net:1 first. Resources are exclusively this process's UUID-labeled
containers/networks/tables. All probe payloads execute INSIDE containers. The
only host operations are trusted Docker orchestration and checking JSON facts.
"""
from __future__ import annotations

import argparse
import ipaddress
import ipaddress
import json
import os
import sys
import time
import uuid
from pathlib import Path

import docker
from docker.types import IPAMConfig, IPAMPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.executors import egress_network as runtime
from sandbox_runtime import ERROR_STAGES, failure_evidence, record_execution_id

IMAGE = os.environ.get("AW_SBX_NET_IMAGE", "aw-sbx-net:1")
USER_IMAGE = os.environ.get("AW_SBX_PROBE_IMAGE", "aw-sandbox-python:3.12")
MANDATORY_CASES = frozenset({
    "unbound-direct-ip-denied", "wildcard-bind-before-answer", "tcp-dns-full-cname-chain",
    "ipv6-domain-binding", "domain-port-restriction", "udp-domain-binding",
    "unknown-domain-and-ttl-expiry", "execution-isolation", "stalled-gateway-health-and-kernel-lease",
    "gateway-death-health-lease", "cname-blacklist-priority", "shared-address-deny-priority",
    "cname-denial-attributable", "fragmented-datagram-fails-closed", "private-rebinding-refused",
    "dns-refusals-and-actual-packets-audited", "fragment-target-headers-audited",
    "owned-resources-and-kernel-tables-cleaned",
})
MAX_REPORT_BYTES = 128 * 1024


def finite_failure(exc, stage):
    """Only shared finite enums/daemon status escape this reporting boundary."""
    if stage not in ERROR_STAGES:
        stage = "unknown"
    try:
        evidence = failure_evidence(exc)
    except Exception:  # noqa: BLE001 - even a broken exception formatter must not lose the report
        evidence = {"error_stage": "unknown", "error_code": "unknown", "error_type": "OtherError",
                    "error_chain": ["OtherError"]}
    if evidence["error_stage"] == "unknown":
        evidence["error_stage"] = stage
    return evidence

TARGET = r'''
import socket,threading,time

def serve(family,port,udp=False):
 s=socket.socket(family,socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
 if family==socket.AF_INET6:s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
 s.bind(('::' if family==socket.AF_INET6 else '0.0.0.0',port))
 if not udp:s.listen(16)
 while True:
  if udp:
   data,peer=s.recvfrom(128);s.sendto(b'ok',peer)
  else:
   c,_=s.accept();c.sendall(b'ok');c.close()
for f in (socket.AF_INET,socket.AF_INET6):
 for p,u in ((8080,False),(8081,False),(9090,True)):
  threading.Thread(target=serve,args=(f,p,u),daemon=True).start()
print('target-ready',flush=True);time.sleep(120)
'''

UPSTREAM = r'''
import sys,socket,json,time
sys.path.insert(0,'/opt/aw')
from domain_policy import question,response,Record
cfg=json.loads(sys.argv[1]);s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind(('0.0.0.0',53));s.settimeout(.2)
print('upstream-ready',flush=True);end=time.monotonic()+120
while time.monotonic()<end:
 try:wire,peer=s.recvfrom(4096)
 except socket.timeout:continue
 q=question(wire);d=q.domain
 if d=='cname.example.test':records=[Record(d,5,2,'cdn.vendor.test')]
 elif d=='evil.example.test':records=[Record(d,5,2,'blocked.vendor.test'),Record('blocked.vendor.test',q.kind,2,cfg[str(q.kind)])]
 elif d=='private.example.test':records=[Record(d,q.kind,2,'127.0.0.1' if q.kind==1 else '::1')]
 elif d=='zero.example.test':records=[Record(d,q.kind,0,cfg[str(q.kind)])]
 elif d=='loop.example.test':records=[Record(d,5,2,d)]
 else:records=[Record(d,q.kind,2,cfg[str(q.kind)])]
 s.sendto(response(q,records),peer)
'''

QUERY = r'''
import socket,struct,json,sys,time
c=json.loads(sys.argv[1]);d=c.get('name','good.example.test');kind=c.get('kind',1)
def query():
 body=b''.join(bytes([len(x)])+x.encode() for x in d.split('.'))+b'\0'+struct.pack('!HH',kind,1)
 wire=struct.pack('!6H',1234,256,1,0,0,0)+body
 s=socket.socket(socket.AF_INET,socket.SOCK_STREAM if c.get('tcp') else socket.SOCK_DGRAM);s.settimeout(1)
 try:
  if c.get('tcp'):
   s.connect((c['dns'],53));s.sendall(struct.pack('!H',len(wire))+wire);h=s.recv(2);data=s.recv(struct.unpack('!H',h)[0])
  else:s.sendto(wire,(c['dns'],53));data=s.recv(4096)
  return struct.unpack_from('!H',data,2)[0]&15
 except OSError:return -1
 finally:s.close()
def connect():
 s=socket.socket(socket.AF_INET6 if kind==28 else socket.AF_INET,socket.SOCK_DGRAM if c.get('udp') else socket.SOCK_STREAM);s.settimeout(.35)
 try:
  s.connect((c['target'],c.get('port',8080)))
  if c.get('udp'):s.send(b'probe')
  return s.recv(2)==b'ok'
 except OSError:return False
 finally:s.close()
rcode=query() if c.get('query',True) else None
if c.get('delay'):time.sleep(c['delay'])
print(json.dumps({'rcode':rcode,'connected':connect()}))
'''


def domain(target, action="allow", ports="8080-9090"):
    return {"id": target, "action": action, "target_type": "domain", "target": target, "port_range": ports}


def ready(container, marker):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        logs = container.logs(tail=20)
        if not isinstance(logs, bytes) or len(logs) > 65536:
            raise RuntimeError("owned fixture readiness exceeded bounded log budget")
        if marker.encode() in logs:
            return
        time.sleep(.02)
    raise RuntimeError("owned fixture readiness deadline exceeded")


class Probe:
    def __init__(self):
        # Docker initialization belongs inside main's guarded run/cleanup path.
        self.client = None
        self.phase = "fixture_config"
        self.owner = str(uuid.uuid4())
        self.labels = {"aw.domain.probe": self.owner}
        if os.environ.get("AW_VERIFY_RUN_ID"):
            self.labels["aw.verify_run"] = os.environ["AW_VERIFY_RUN_ID"]
        self.containers = []
        self.networks = []
        self.captures = []
        self.execution_ids = []
        self.results = []
        self.offset = uuid.UUID(self.owner).int % 180 + 20
        self.original_dns = runtime.EGRESS_DNS_PROXY

    def network(self, index, *, target=False):
        self.phase = "fixture_network_create"
        third = (self.offset + index) % 240 + 1
        ipv4 = f"198.18.{third}.0/24" if target else f"198.19.{third}.0/24"
        ipv6 = f"2001:db8:{uuid.UUID(self.owner).int & 0xffff:x}:{index + int(target) * 100:x}::/64"
        net = self.client.networks.create(
            f"aw-domain-probe-{self.owner}-{index}-{'t' if target else 's'}", driver="bridge",
            enable_ipv6=True, labels=self.labels,
            # Docker 28 may keep an implicit gateway out of IPAM.Config even
            # after reload; define both owned fixture gateways explicitly.
            ipam=IPAMConfig(pool_configs=[
                IPAMPool(subnet=subnet, gateway=str(ipaddress.ip_network(subnet).network_address + 1))
                for subnet in (ipv4, ipv6)
            ]),
        )
        self.networks.append(net)
        net.reload()
        return net

    def container(self, image, **kwargs):
        self.phase = "fixture_container_create"
        container = self.client.containers.run(image, labels=self.labels, detach=True, remove=False,
            read_only=True, cap_drop=["ALL"], security_opt=["no-new-privileges"],
            mem_limit="64m", pids_limit=32, **kwargs)
        self.containers.append(container)
        return container

    def check(self, name, condition, _facts):
        if name not in MANDATORY_CASES:
            raise ValueError("unknown domain acceptance case")
        result = {"name": name, "passed": bool(condition)}
        if not condition:
            result["runtime"] = finite_failure(AssertionError(), self.phase)
        self.results.append(result)
        if not condition:
            raise AssertionError("domain acceptance assertion failed")

    def record_failure(self, name, exc, *, stage=None):
        if name not in {"runtime-exception", "cleanup-exception", "mandatory-cases-incomplete"}:
            raise ValueError("unknown domain failure case")
        self.results.append({"name": name, "passed": False,
                             "runtime": finite_failure(exc, stage or self.phase)})

    def query(self, user, capture, target4, target6, **values):
        self.phase = "fixture_probe"
        kind = values.get("kind", 1)
        payload = {"dns": capture.dns_proxy_ip, "target": target6 if kind == 28 else target4, **values}
        result = user.exec_run(["python", "-c", QUERY, json.dumps(payload)])
        if result.exit_code:
            raise RuntimeError("owned fixture query process failed")
        if not isinstance(result.output, bytes) or len(result.output) > 4096:
            raise ValueError("owned fixture query exceeded bounded result budget")
        fact = json.loads(result.output)
        if not isinstance(fact, dict) or set(fact) != {"rcode", "connected"}:
            raise ValueError("owned fixture query returned invalid facts")
        # The immutable QUERY payload deliberately performs no DNS lookup for
        # direct-IP/isolation/lease tests, and reports rcode=None in that branch.
        rcode = fact["rcode"]
        valid_rcode = (rcode is None if values.get("query", True) is False
                       else type(rcode) is int and -1 <= rcode <= 15)
        if not valid_rcode or type(fact["connected"]) is not bool:
            raise ValueError("owned fixture query returned invalid facts")
        return fact

    def execution(self, index, target4, target6, *, mode="restricted", global_rules=None):
        net = self.network(index)
        upstream = self.container(IMAGE, network=self.targetnet.name, cap_add=["NET_BIND_SERVICE"],
            name=f"aw-domain-upstream-{self.owner}-{index}",
            command=["python3", "-u", "-c", UPSTREAM, json.dumps({"1": target4, "28": target6})])
        self.phase = "fixture_listener_ready"
        ready(upstream, "upstream-ready")
        runtime.EGRESS_DNS_PROXY = upstream.name
        eid = str(uuid.uuid4())
        self.execution_ids.append(eid)
        record_execution_id(eid)
        self.phase = "policy_install"
        capture = runtime.NetworkCapture.prepare(self.client, net, eid, {
            "mode": mode, "global_rules": global_rules or [],
            "agent_rules": [domain("*.example.test", ports="8080"), domain("*.example.test", ports="9090")],
            "builtin_blacklist": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        })
        self.captures.append(capture)
        endpoint = {"IPv4Address": capture.source_ip, "IPv6Address": capture.source_ipv6}
        self.phase = "container_create"
        user = self.client.containers.create(USER_IMAGE, name=f"aw-domain-user-{self.owner}-{index}",
            command=["python", "-u", "-c", "import time;print('user-ready',flush=True);time.sleep(100)"],
            network=net.name, networking_config={net.name: {"IPAMConfig": endpoint}},
            dns=[capture.dns_proxy_ip], user="10001:10001", read_only=True, cap_drop=["ALL"],
            security_opt=["no-new-privileges"], mem_limit="64m", pids_limit=16, labels=self.labels)
        self.containers.append(user)
        self.phase = "output_capture"
        capture.start_capture(user)
        self.phase = "container_start"
        user.start()
        self.phase = "fixture_listener_ready"
        ready(user, "user-ready")
        return user, capture

    def run(self):
        self.phase = "docker_connect"
        self.client = docker.from_env(timeout=5)
        targetnet = self.network(1, target=True)
        self.targetnet = targetnet
        target = self.container(IMAGE, network=targetnet.name, command=["python3", "-u", "-c", TARGET])
        self.phase = "fixture_listener_ready"
        ready(target, "target-ready")
        self.phase = "fixture_config"
        target.reload()
        info = target.attrs["NetworkSettings"]["Networks"][targetnet.name]
        v4, v6 = info["IPAddress"], info["GlobalIPv6Address"]
        if ipaddress.ip_address(v4).version != 4 or ipaddress.ip_address(v6).version != 6:
            raise ValueError("owned target must have real IPv4 and IPv6 addresses")
        user, cap = self.execution(2, v4, v6)
        result = self.query(user, cap, v4, v6, query=False)
        self.check("unbound-direct-ip-denied", not result["connected"], result)
        result = self.query(user, cap, v4, v6)
        self.check("wildcard-bind-before-answer", result == {"rcode": 0, "connected": True}, result)
        result = self.query(user, cap, v4, v6, name="cname.example.test", tcp=True)
        self.check("tcp-dns-full-cname-chain", result == {"rcode": 0, "connected": True}, result)
        result = self.query(user, cap, v4, v6, kind=28)
        self.check("ipv6-domain-binding", result == {"rcode": 0, "connected": True}, result)
        result = self.query(user, cap, v4, v6, port=8081)
        self.check("domain-port-restriction", result["rcode"] == 0 and not result["connected"], result)
        result = self.query(user, cap, v4, v6, udp=True, port=9090)
        self.check("udp-domain-binding", result == {"rcode": 0, "connected": True}, result)
        result = self.query(user, cap, v4, v6, name="not-allowed.test", query=True, delay=2.2)
        self.check("unknown-domain-and-ttl-expiry", result["rcode"] == 5 and not result["connected"], result)
        other, other_cap = self.execution(3, v4, v6)
        result = self.query(user, cap, v4, v6)
        result2 = self.query(other, other_cap, v4, v6, query=False)
        self.check("execution-isolation", result["connected"] and not result2["connected"], result2)
        authorized = self.query(other, other_cap, v4, v6)
        other_cap.domain_gateway.kill(signal="SIGSTOP")
        time.sleep(1.05)
        unhealthy = False
        try:
            other_cap.assert_healthy()
        except runtime.NetworkPolicyError:
            unhealthy = True
        stalled = self.query(other, other_cap, v4, v6, query=False)
        self.check("stalled-gateway-health-and-kernel-lease", authorized["connected"] and unhealthy and not stalled["connected"], stalled)
        other_cap.domain_gateway.kill(signal="SIGKILL")
        start = time.monotonic()
        cap.domain_gateway.kill(signal="SIGKILL")
        time.sleep(1.05)
        result = self.query(user, cap, v4, v6, query=False)
        self.check("gateway-death-health-lease", not result["connected"] and time.monotonic() - start < 2.2, result)
        denied_user, denied_cap = self.execution(4, v4, v6, global_rules=[domain("*.vendor.test", "deny", None)])
        result = self.query(denied_user, denied_cap, v4, v6, name="evil.example.test")
        self.check("cname-blacklist-priority", result["rcode"] == 5 and not result["connected"], result)
        result = self.query(denied_user, denied_cap, v4, v6)
        self.check("shared-address-deny-priority", result["rcode"] == 0 and not result["connected"], result)
        self.phase = "audit_collect"
        events = denied_cap._domain_events()
        self.check("cname-denial-attributable", any(e["domain"] == "evil.example.test" and e["action"] == "deny" for e in events), len(events))
        rebound, rebound_cap = self.execution(5, v4, v6)
        authorized = self.query(rebound, rebound_cap, v4, v6, udp=True, port=9090)
        fragment_code = (
            "import socket,sys,json;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
            "s.setsockopt(socket.IPPROTO_IP,10,0);s.settimeout(.4);s.connect((sys.argv[1],9090));"
            "s.send(b'x'*4000);\ntry:r=s.recv(2)\nexcept OSError:r=b''\n"
            "print(json.dumps({'reply':bool(r)}))"
        )
        fragmented = rebound.exec_run(["python", "-c", fragment_code, v4])
        if fragmented.exit_code or not isinstance(fragmented.output, bytes) or len(fragmented.output) > 4096:
            raise RuntimeError("owned fragment probe process failed")
        fragment_fact = json.loads(fragmented.output)
        if not isinstance(fragment_fact, dict) or set(fragment_fact) != {"reply"} or type(fragment_fact["reply"]) is not bool:
            raise ValueError("owned fragment probe returned invalid facts")
        self.check("fragmented-datagram-fails-closed", authorized["connected"] and not fragment_fact["reply"], fragment_fact)
        result = self.query(rebound, rebound_cap, v4, v6, name="private.example.test")
        # Existing public bindings remain valid; the refused private answer
        # itself is never delivered or installed as a permission.
        self.check("private-rebinding-refused", result["rcode"] == 5, result)
        # Collect every complete packet log; fatal gateway has an intact audit log.
        self.phase = "audit_collect"
        records, denied = denied_cap.collect()
        self.check("dns-refusals-and-actual-packets-audited", denied and any(r["domain"] == "evil.example.test" and r["action"] == "deny" for r in records), len(records))
        records, denied = rebound_cap.collect()
        self.check("fragment-target-headers-audited", denied and any(r["protocol"] == "fragment" and r["port"] == 0 and r["action"] == "deny" and r["target_ip"] == v4 for r in records), len(records))
        return self.results

    def close(self):
        self.phase = "fixture_cleanup"
        runtime.EGRESS_DNS_PROXY = self.original_dns
        if self.client is None:
            self.check("owned-resources-and-kernel-tables-cleaned",
                       not (self.containers or self.captures or self.networks), {})
            return
        failed = False

        def attempt(action, stage="fixture_cleanup"):
            nonlocal failed
            self.phase = stage
            try:
                return True, action()
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - finish every owned cleanup
                failed = True
                self.record_failure("cleanup-exception", exc, stage=stage)
                return False, None

        residual = {}
        try:
            filters = {"label": f"aw.domain.probe={self.owner}"}
            # A Docker run/create timeout may occur after creating a labelled
            # resource but before returning its handle. Discover only this UUID.
            for collection, known, options in (
                (self.client.containers, self.containers, {"all": True}),
                (self.client.networks, self.networks, {}),
            ):
                ok, discovered = attempt(lambda source=collection, kwargs=options: source.list(filters=filters, **kwargs))
                if ok:
                    for item in discovered:
                        if not any(existing.id == item.id for existing in known):
                            known.append(item)
            # Preserve the fail-closed teardown order: users, captures, networks.
            for container in reversed(self.containers):
                attempt(lambda item=container: item.remove(force=True))
            for capture in reversed(self.captures):
                attempt(capture.cleanup)
            for network in reversed(self.networks):
                attempt(network.remove)
            tables = [prefix + uuid.UUID(eid).hex for eid in self.execution_ids for prefix in ("awd_", "awp_")]
            chains = [chain + suffix for eid in self.execution_ids for chain in runtime._chain_names(eid) for suffix in ("", "6")]
            inspect_code = (
                "import json,subprocess,sys;c=json.loads(sys.argv[1]);out=[];"
                "\nfor cmd in (['nft','list','tables'],['iptables-save'],['ip6tables-save']):"
                "\n p=subprocess.run(cmd,capture_output=True,timeout=2,check=True);out.append(p.stdout.decode())"
                "\nprint(json.dumps({'kernel_residuals':sum(any(n in x for x in out) for n in c)}))"
            )

            def kernel_count():
                raw = self.client.containers.run(IMAGE, command=["python3", "-c", inspect_code, json.dumps(tables + chains)],
                    network_mode="host", cap_drop=["ALL"], cap_add=["NET_ADMIN"], read_only=True,
                    security_opt=["no-new-privileges"], mem_limit="64m", pids_limit=16,
                    remove=True, labels=self.labels,
                    log_config=docker.types.LogConfig(type="json-file", config={"max-size": "256k", "max-file": "1"}))
                if not isinstance(raw, bytes) or len(raw) > 4096:
                    raise ValueError("owned kernel check exceeded bounded result budget")
                value = json.loads(raw)
                if (not isinstance(value, dict) or set(value) != {"kernel_residuals"}
                        or type(value["kernel_residuals"]) is not int
                        or not 0 <= value["kernel_residuals"] <= len(tables + chains)):
                    raise ValueError("owned kernel check returned invalid facts")
                return value["kernel_residuals"]

            ok, value = attempt(kernel_count)
            if ok:
                residual["kernel_rules"] = value
            ok, value = attempt(lambda: len(self.client.containers.list(all=True, filters=filters)))
            if ok:
                residual["containers"] = value
            ok, value = attempt(lambda: len(self.client.networks.list(filters=filters)))
            if ok:
                residual["networks"] = value
            passed = not failed and len(residual) == 3 and not any(residual.values())
            evidence = {"residual": residual}
            if not passed:
                evidence.update(finite_failure(AssertionError(), "fixture_cleanup"))
            self.results.append({"name": "owned-resources-and-kernel-tables-cleaned",
                                 "passed": passed, "runtime": evidence})
        finally:
            attempt(self.client.close, stage="fixture_client_close")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    probe = Probe()
    try:
        probe.run()
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - secret-safe CI report boundary
        probe.record_failure("runtime-exception", exc)
    finally:
        try:
            probe.close()
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - cleanup never suppresses evidence
            probe.record_failure("cleanup-exception", exc, stage="fixture_cleanup")
    completed = {item["name"] for item in probe.results if item.get("passed") is True}
    if not MANDATORY_CASES.issubset(completed):
        probe.record_failure("mandatory-cases-incomplete", AssertionError(), stage="fixture_probe")
    passed = bool(probe.results) and all(item["passed"] is True for item in probe.results)
    report = {"suite": "s5_domain_runtime", "results": probe.results, "execution_ids": probe.execution_ids}
    text = json.dumps(report, indent=2) + "\n"
    if len(text.encode()) > MAX_REPORT_BYTES:
        # Even an unexpected reporting explosion must fail with bounded evidence.
        text = json.dumps({"suite": "s5_domain_runtime", "execution_ids": [], "results": [{
            "name": "runtime-exception", "passed": False,
            "runtime": finite_failure(RuntimeError(), "fixture_config"),
        }]}) + "\n"
        passed = False
    if args.output:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(text)
    print(text, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
