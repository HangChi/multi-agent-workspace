#!/usr/bin/env python3
"""Owned, deterministic T060 Linux-kernel probe; no DB, .env, public DNS or TLS.

Run from repository root: PYTHONPATH=backend backend/.venv/bin/python deploy/verify/s5_domain_runtime.py
Build aw-sbx-net:1 first. Resources are exclusively this process's UUID-labeled
containers/networks/tables. All probe payloads execute INSIDE containers. The
only host operations are trusted Docker orchestration and checking JSON facts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import docker
from docker.types import IPAMConfig, IPAMPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from sandbox_runtime import record_execution_id

from app.executors import egress_network as runtime

IMAGE = os.environ.get("AW_SBX_NET_IMAGE", "aw-sbx-net:1")
USER_IMAGE = os.environ.get("AW_SBX_PROBE_IMAGE", "aw-sandbox-python:3.12")

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
        if marker.encode() in container.logs():
            return
        time.sleep(.02)
    raise AssertionError(f"owned probe did not start: {marker}: {container.logs()!r}")


class Probe:
    def __init__(self):
        self.client = docker.from_env(timeout=5)
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
        third = (self.offset + index) % 240 + 1
        ipv4 = f"198.18.{third}.0/24" if target else f"198.19.{third}.0/24"
        ipv6 = f"2001:db8:{uuid.UUID(self.owner).int & 0xffff:x}:{index + int(target) * 100:x}::/64"
        net = self.client.networks.create(
            f"aw-domain-probe-{self.owner}-{index}-{'t' if target else 's'}", driver="bridge",
            enable_ipv6=True, labels=self.labels,
            ipam=IPAMConfig(pool_configs=[IPAMPool(subnet=ipv4), IPAMPool(subnet=ipv6)]),
        )
        self.networks.append(net)
        net.reload()
        return net

    def container(self, image, **kwargs):
        container = self.client.containers.run(image, labels=self.labels, detach=True, remove=False,
            read_only=True, cap_drop=["ALL"], security_opt=["no-new-privileges"],
            mem_limit="64m", pids_limit=32, **kwargs)
        self.containers.append(container)
        return container

    def check(self, name, condition, facts):
        self.results.append({"name": name, "passed": bool(condition), "facts": facts})
        if not condition:
            raise AssertionError(f"{name}: {facts}")

    def query(self, user, capture, target4, target6, **values):
        kind = values.get("kind", 1)
        payload = {"dns": capture.dns_proxy_ip, "target": target6 if kind == 28 else target4, **values}
        result = user.exec_run(["python", "-c", QUERY, json.dumps(payload)])
        if result.exit_code:
            raise AssertionError(result.output.decode())
        return json.loads(result.output)

    def execution(self, index, target4, target6, *, mode="restricted", global_rules=None):
        net = self.network(index)
        upstream = self.container(IMAGE, network=self.targetnet.name, cap_add=["NET_BIND_SERVICE"],
            name=f"aw-domain-upstream-{self.owner}-{index}",
            command=["python3", "-u", "-c", UPSTREAM, json.dumps({"1": target4, "28": target6})])
        ready(upstream, "upstream-ready")
        runtime.EGRESS_DNS_PROXY = upstream.name
        eid = str(uuid.uuid4())
        self.execution_ids.append(eid)
        record_execution_id(eid)
        capture = runtime.NetworkCapture.prepare(self.client, net, eid, {
            "mode": mode, "global_rules": global_rules or [],
            "agent_rules": [domain("*.example.test", ports="8080"), domain("*.example.test", ports="9090")],
            "builtin_blacklist": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        })
        self.captures.append(capture)
        endpoint = {"IPv4Address": capture.source_ip, "IPv6Address": capture.source_ipv6}
        user = self.client.containers.create(USER_IMAGE, name=f"aw-domain-user-{self.owner}-{index}",
            command=["python", "-u", "-c", "import time;print('user-ready',flush=True);time.sleep(100)"],
            network=net.name, networking_config={net.name: {"IPAMConfig": endpoint}},
            dns=[capture.dns_proxy_ip], user="10001:10001", read_only=True, cap_drop=["ALL"],
            security_opt=["no-new-privileges"], mem_limit="64m", pids_limit=16, labels=self.labels)
        self.containers.append(user)
        capture.start_capture(user)
        user.start()
        ready(user, "user-ready")
        return user, capture

    def run(self):
        targetnet = self.network(1, target=True)
        self.targetnet = targetnet
        target = self.container(IMAGE, network=targetnet.name, command=["python3", "-u", "-c", TARGET])
        ready(target, "target-ready")
        target.reload()
        info = target.attrs["NetworkSettings"]["Networks"][targetnet.name]
        v4, v6 = info["IPAddress"], info["GlobalIPv6Address"]
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
        fragment_fact = json.loads(fragmented.output)
        self.check("fragmented-datagram-fails-closed", authorized["connected"] and not fragment_fact["reply"], fragment_fact)
        result = self.query(rebound, rebound_cap, v4, v6, name="private.example.test")
        # Existing public bindings remain valid; the refused private answer
        # itself is never delivered or installed as a permission.
        self.check("private-rebinding-refused", result["rcode"] == 5, result)
        # Collect every complete packet log; fatal gateway has an intact audit log.
        records, denied = denied_cap.collect()
        self.check("dns-refusals-and-actual-packets-audited", denied and any(r["domain"] == "evil.example.test" and r["action"] == "deny" for r in records), len(records))
        records, denied = rebound_cap.collect()
        self.check("fragment-target-headers-audited", denied and any(r["protocol"] == "fragment" and r["port"] == 0 and r["action"] == "deny" and r["target_ip"] == v4 for r in records), len(records))
        return self.results

    def close(self):
        runtime.EGRESS_DNS_PROXY = self.original_dns
        # Remove user processes BEFORE deleting fail-closed execution rules.
        for container in reversed(self.containers):
            try: container.remove(force=True)
            except docker.errors.DockerException: pass
        for capture in reversed(self.captures):
            try: capture.cleanup()
            except docker.errors.DockerException: pass
        for network in reversed(self.networks):
            try: network.remove()
            except docker.errors.DockerException: pass
        tables = [prefix + uuid.UUID(eid).hex for eid in self.execution_ids for prefix in ("awd_", "awp_")]
        chains = [chain + suffix for eid in self.execution_ids for chain in runtime._chain_names(eid) for suffix in ("", "6")]
        inspect_code = (
            "import json,subprocess,sys;c=json.loads(sys.argv[1]);out=[];"
            "\nfor cmd in (['nft','list','tables'],['iptables-save'],['ip6tables-save']):"
            "\n p=subprocess.run(cmd,capture_output=True,timeout=2,check=True);out.append(p.stdout.decode())"
            "\nprint(json.dumps({'kernel_residuals':sum(any(n in x for x in out) for n in c)}))"
        )
        raw = self.client.containers.run(IMAGE, command=["python3", "-c", inspect_code, json.dumps(tables + chains)],
            network_mode="host", cap_drop=["ALL"], cap_add=["NET_ADMIN"], read_only=True,
            security_opt=["no-new-privileges"], mem_limit="64m", pids_limit=16,
            remove=True, labels=self.labels,
            log_config=docker.types.LogConfig(type="json-file", config={"max-size": "256k", "max-file": "1"}))
        residual = json.loads(raw)
        residual["container_residuals"] = len(self.client.containers.list(all=True, filters={"label": f"aw.domain.probe={self.owner}"}))
        residual["network_residuals"] = len(self.client.networks.list(filters={"label": f"aw.domain.probe={self.owner}"}))
        self.check("owned-resources-and-kernel-tables-cleaned", not any(residual.values()), residual)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    probe = Probe()
    failure = None
    try:
        probe.run()
    except (AssertionError, RuntimeError, ValueError, OSError, docker.errors.DockerException) as exc:
        failure = exc
        causes = [str(exc)]
        cause = exc.__cause__
        while cause is not None and len(causes) < 4:
            diagnostic = getattr(cause, "stderr", None)
            causes.append(diagnostic.decode("utf-8", "replace")[:4096] if isinstance(diagnostic, bytes) else str(cause)[-4096:])
            cause = cause.__cause__
        probe.results.append({"name": "runtime-exception", "passed": False, "facts": causes})
    finally:
        try:
            probe.close()
        except (AssertionError, RuntimeError, ValueError, OSError, docker.errors.DockerException) as exc:
            failure = failure or exc
            probe.results.append({"name": "cleanup-exception", "passed": False, "facts": str(exc)})
    report = {"suite": "s5_domain_runtime", "results": probe.results, "execution_ids": probe.execution_ids}
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    if failure is not None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
