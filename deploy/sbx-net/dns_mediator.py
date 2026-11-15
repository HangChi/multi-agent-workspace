"""Per-execution trusted DNS -> nft mediator. Launched only by fixed argv.

Host network/admin privileges are isolated from the user container. No Docker
socket, mounted host files, application secrets, user code, or shared DNS state.
The kernel checks a <=1s health lease and TTL on every packet. Process crash,
OOM, upstream failure, malformed answers, binding failure and budget exhaustion
cannot produce a usable answer. The short lease is NOT a fail-open grace period:
only already-authorized traffic can pass until its existing lease expires.
"""
from __future__ import annotations

import base64
import errno
import ipaddress
import json
import os
import selectors
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid

from domain_policy import (
    MAX_BINDINGS,
    MAX_CHAIN,
    MAX_WIRE,
    Policy,
    PolicyError,
    Question,
    answer,
    chain,
    nft_policy,
    question,
    response,
    table_name,
)

MAX_REQUESTS = 4096
MAX_EVENTS = 8192
MAX_WORKERS = 8


DIAGNOSTIC_STAGES = frozenset({
    "dns_startup", "dns_socket_bind", "dns_nft_install", "dns_nft_heartbeat",
    "dns_health_write", "dns_runtime", "dns_health_check", "dns_audit", "packet_audit",
})
DIAGNOSTIC_CODES = frozenset({
    "unknown", "permission_denied", "address_in_use", "address_unavailable",
    "missing_executable", "invalid_argument", "no_space_left", "operation_timeout",
    "nft_syntax", "nft_feature_unavailable", "nft_transaction_failed", "helper_exited",
    "helper_not_ready", "health_expired", "helper_paused", "helper_oom", "diagnostic_invalid",
    "capture_exited", "capture_incomplete", "capture_truncated",
})


class GatewayFailure(PolicyError):
    """Finite trusted cause; never retain a failed command's output."""

    def __init__(self, code: str):
        self.code = code if code in DIAGNOSTIC_CODES else "unknown"
        super().__init__(self.code)


def failure_code(failure: BaseException) -> str:
    if isinstance(failure, GatewayFailure):
        return failure.code
    if isinstance(failure, (TimeoutError, subprocess.TimeoutExpired)):
        return "operation_timeout"
    if isinstance(failure, OSError):
        return {errno.EPERM: "permission_denied", errno.EACCES: "permission_denied",
                errno.EADDRINUSE: "address_in_use", errno.EADDRNOTAVAIL: "address_unavailable",
                errno.ENOENT: "missing_executable", errno.EINVAL: "invalid_argument",
                errno.ENOSPC: "no_space_left"}.get(failure.errno, "unknown")
    return "unknown"


def report_failure(config: dict, stage: str, failure: BaseException) -> None:
    """Only fixed enums plus validated execution identity may cross this boundary."""
    try:
        execution_id = str(uuid.UUID(config["execution_id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        return
    print(json.dumps({"aw_dns_failure": 1, "execution_id": execution_id,
                      "stage": stage if stage in DIAGNOSTIC_STAGES else "dns_startup",
                      "code": failure_code(failure)}, separators=(",", ":")), flush=True)


def nft(script: str) -> None:
    try:
        result = subprocess.run(["nft", "-f", "-"], input=script.encode(), capture_output=True, timeout=0.5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GatewayFailure(failure_code(exc)) from None
    if result.returncode:
        # Classify privately; stderr can contain addresses, domain names and the
        # entire generated ruleset, so never attach it to an exception or event.
        text = (result.stderr or b"")[:8192].decode("utf-8", "replace").lower()
        code = next((code for markers, code in (
            (("operation not permitted", "permission denied"), "permission_denied"),
            (("syntax error",), "nft_syntax"),
            (("operation not supported", "not supported", "no such file or directory"), "nft_feature_unavailable"),
            (("invalid argument",), "invalid_argument"),
            (("no space left",), "no_space_left"),
        ) if any(marker in text for marker in markers)), "nft_transaction_failed")
        raise GatewayFailure(code)


class Binder:
    def __init__(self, config: dict, policy: Policy, *, execute=nft, clock=time.monotonic):
        self.config, self.policy = config, policy
        self.table = table_name(config["execution_id"])
        self.execute, self.clock = execute, clock
        self.lock = threading.Lock()
        self.bindings: dict[tuple[str, str], float] = {}

    def install(self) -> None:
        self.execute(nft_policy(self.config, self.policy))

    def heartbeat(self) -> None:
        rows = []
        for family, key in ((4, "source_ip"), (6, "source_ipv6")):
            if self.config.get(key):
                ip = str(ipaddress.ip_address(self.config[key]))
                rows.extend([f"flush set inet {self.table} alive{family}",
                             f"add element inet {self.table} alive{family} {{ {ip} timeout 1s }}"])
        with self.lock:
            self.execute("\n".join(rows) + "\n")

    def bind(self, addresses: list[str], names: list[str], original: str, ttl: int,
             *, valid_until: float | None = None) -> tuple[bool, str | None]:
        """Commit ALL deny/allow/seen elements atomically, then release the answer.

        Timeout is rounded DOWN and response TTL is capped to the same value.
        Entries are never evicted to make room: saturation refuses DNS. A denied
        answer still installs its blacklist entries (before emitting REFUSED).
        """
        denies = self.policy.denies(names)
        allows = self.policy.allows(original)
        safe = all(self.policy.safe(ip) for ip in addresses)
        full_deny = any(not self.policy.global_rules[i].get("port_range") for i in denies)
        permitted = safe and not full_deny and self.policy.permitted_query(original)
        rule_id = self.policy.global_rules[denies[0]].get("id") if denies else None
        ttl = min(300, int(ttl))
        if ttl < 1:
            raise PolicyError("zero TTL cannot safely authorize a kernel lease")
        # Keep the DNS answer's absolute validity across lock contention and the
        # bounded kernel transaction. Never restart a TTL after waiting to bind.
        valid_until = min(self.clock() + 300, valid_until) if valid_until is not None else self.clock() + ttl
        values = []
        for raw in addresses:
            ip = ipaddress.ip_address(raw)
            # Unsafe DNS never grants a lease. Global IP/CIDR and builtin drops
            # also precede every allow in the independently compiled kernel ACL.
            if self.policy.safe(str(ip)):
                values.extend((f"d{i}_{ip.version}", str(ip)) for i in denies)
                if permitted:
                    values.append((f"seen{ip.version}", str(ip)))
                    values.extend((f"a{i}_{ip.version}", str(ip)) for i in allows)
        with self.lock:
            now = self.clock()
            if valid_until - now <= 0.5:
                raise PolicyError("DNS lease expired while waiting for kernel binding")
            retained = {key: expiry for key, expiry in self.bindings.items() if expiry > now}
            # Bounded total across ALL sets, not just per-set maximum.
            if len(set(retained) | set(values)) > MAX_BINDINGS:
                raise PolicyError("execution DNS binding budget exhausted")
            touched = set()
            for key in dict.fromkeys(values):
                # A second name must never shorten an existing deny lease.
                retained[key] = max(valid_until, retained.get(key, 0))
                touched.add(key[0])
            rows = []
            for set_name in sorted(touched):
                # Userspace nft has no `update element` command. Replacing the
                # touched sets in ONE transaction refreshes TTLs without a gap,
                # and never races a delete against kernel timeout expiration.
                rows.append(f"flush set inet {self.table} {set_name}")
                # Reserve the full 0.5s command timeout for allow leases. Deny
                # leases may conservatively outlive an answer, never grant one.
                margin = 0 if set_name.startswith("d") else 0.5
                elements = [f"{key[1]} timeout {max(1, int((expiry - now - margin) * 1000))}ms"
                            for key, expiry in retained.items()
                            if key[0] == set_name and expiry - now > margin]
                if elements:
                    rows.append(f"add element inet {self.table} {set_name} {{ " + ", ".join(elements) + " }")
            if rows:
                self.execute("\n".join(rows) + "\n")
            self.bindings = retained
        return permitted, rule_id


class Mediator:
    def __init__(self, config: dict, *, resolver=None, binder=None, clock=time.monotonic):
        self.config = config
        self.clock = clock
        self.policy = Policy(config)
        self.binder = binder or Binder(config, self.policy)
        self.resolver = resolver or self.resolve
        self.dead = threading.Event()
        self.slots = threading.BoundedSemaphore(MAX_WORKERS)
        self.lock = threading.Lock()
        self.requests = self.events = 0
        self.started = time.monotonic()
        self.failure_lock = threading.Lock()
        self.failure_reported = False

    def fail(self, stage: str, failure: BaseException) -> None:
        self.dead.set()
        with self.failure_lock:
            if not self.failure_reported:
                self.failure_reported = True
                report_failure(self.config, stage, failure)

    def event(self, **values) -> None:
        with self.lock:
            self.events += 1
            if self.events > MAX_EVENTS:
                self.dead.set()
                raise PolicyError("DNS audit budget exhausted")
            print(json.dumps({"aw_dns": 1, "execution_id": self.config["execution_id"],
                              "time": time.time(), "seq": self.events, **values}, separators=(",", ":")), flush=True)

    def resolve(self, q: Question, deadline: float) -> bytes:
        upstream = str(ipaddress.ip_address(self.config["upstream_ip"]))
        kind = socket.AF_INET6 if ":" in upstream else socket.AF_INET
        wire = q.wire()
        last_timeout: TimeoutError | None = None
        # A single lost UDP request must not turn a healthy mediator into a
        # failure while keeping the total resolver budget bounded by the query
        # deadline. Every attempt remains finite and fail-closed on exhaustion.
        for _ in range(2):
            remaining = min(1.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            try:
                with socket.socket(kind, socket.SOCK_DGRAM) as conn:
                    conn.settimeout(remaining)
                    conn.connect((upstream, 53))
                    conn.send(wire)
                    return conn.recv(MAX_WIRE + 1)
            except TimeoutError as exc:
                last_timeout = exc
        raise last_timeout or PolicyError("DNS resolution deadline exceeded")

    def process(self, wire: bytes, peer: str, *, protocol: str = "udp", source_port: int = 0) -> bytes | None:
        q = None
        transport = {"protocol": protocol, "source_port": source_port}
        try:
            peer = str(ipaddress.ip_address(peer))
            if peer not in (self.config["source_ip"], self.config.get("source_ipv6")):
                return None
            with self.lock:
                self.requests += 1
                if self.requests > MAX_REQUESTS:
                    self.dead.set()
                    raise PolicyError("DNS request budget exhausted")
            q = question(wire)
            if self.dead.is_set():
                return None
            if not self.policy.permitted_query(q.domain) and not self.policy.denies([q.domain]):
                self.event(domain=q.domain, action="deny", reason="not-allowed", matched_rule_id=None, **transport)
                return response(q, [], rcode=5)
            deadline = self.clock() + 3
            current = q.domain
            collected = []
            received = []
            terminal = []
            for _ in range(MAX_CHAIN + 1):
                upstream_q = Question(int.from_bytes(os.urandom(2)), current, q.kind)
                if self.clock() >= deadline:
                    raise PolicyError("DNS CNAME resolution deadline exceeded")
                records, rcode = answer(self.resolver(upstream_q, deadline), upstream_q)
                arrival = self.clock()
                received.extend((r, arrival) for r in records)
                if rcode:
                    # Do not relay upstream authority/additional records or data.
                    return response(q, [], rcode=3 if rcode == 3 else 2)
                collected.extend(records)
                terminal, last = chain(collected, q.domain, q.kind)
                if any(r.kind in (1, 28) for r in terminal):
                    break
                if last == current:
                    return response(q, [])  # genuine NODATA, no binding
                current = last
            else:
                raise PolicyError("CNAME chain too long")
            names = [q.domain, *[r.value for r in terminal if r.kind == 5]]
            addresses = list(dict.fromkeys(r.value for r in terminal if r.kind in (1, 28)))
            # Alias TTL starts when each upstream answer arrived, not when the
            # last CNAME lookup finished. Round DOWN: never extend a short lease
            # by spending time following an attacker-controlled alias chain.
            now = self.clock()
            valid_until = min(arrival + r.ttl for r, arrival in received)
            ttl = int(valid_until - now)
            # Construct sanitized answer BEFORE granting a lease. Oversize or
            # malformed output must not leave an authorization behind.
            sanitized = response(q, terminal, ttl=ttl)
            allowed, matched = self.binder.bind(addresses, names, q.domain, ttl, valid_until=valid_until)
            if allowed:
                ttl = min(ttl, int(valid_until - self.clock() - 0.5))
                if ttl < 1:
                    raise PolicyError("DNS lease expired before answer delivery")
                sanitized = response(q, terminal, ttl=ttl)
            self.event(domain=q.domain, action="allow" if allowed else "deny",
                       reason="bound" if allowed else "blacklist",
                       addresses=[ip for ip in addresses if self.policy.safe(ip)],
                       ttl=ttl, matched_rule_id=matched,
                       denied_rules=[{"id": self.policy.global_rules[i].get("id"),
                                      "port_range": self.policy.global_rules[i].get("port_range")}
                                     for i in self.policy.denies(names)], **transport)
            if self.dead.is_set():
                return None
            return sanitized if allowed else response(q, [], rcode=5)
        except (PolicyError, OSError, TimeoutError, subprocess.SubprocessError, ValueError, struct.error) as exc:
            # A binding failure is fatal: all existing authorizations close when
            # the <=1s health lease expires; never continue in a degraded mode.
            self.fail("dns_runtime", exc)
            if q is not None:
                self.event(domain=q.domain, action="deny", reason="dns-policy-failure", matched_rule_id=None, **transport)
            return None

    def mark_healthy(self, refreshed_after: float) -> None:
        with open("/run/aw-dns-health.tmp", "w", encoding="ascii") as out:
            # Timestamp BEFORE the transaction, publish only AFTER success. A
            # slow nft process must not make the kernel lease appear younger.
            json.dump({"execution_id": self.config["execution_id"], "at": refreshed_after}, out)
        os.replace("/run/aw-dns-health.tmp", "/run/aw-dns-health")

    def heartbeat(self) -> None:
        stage = "dns_nft_heartbeat"
        try:
            while not self.dead.is_set():
                if time.monotonic() - self.started > 660:
                    raise PolicyError("execution gateway lifetime exceeded")
                refreshed_after = time.monotonic()
                stage = "dns_nft_heartbeat"
                self.binder.heartbeat()
                stage = "dns_health_write"
                self.mark_healthy(refreshed_after)
                self.dead.wait(0.2)
        except (PolicyError, OSError, subprocess.SubprocessError) as exc:
            self.fail(stage, exc)

    def serve(self) -> None:
        udp = tcp = selector = thread = None
        stage = "dns_socket_bind"
        try:
            address = str(ipaddress.IPv4Address(self.config["listen_ip"]))
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Do NOT use REUSEADDR/REUSEPORT: a conflicting listener fails startup.
            udp.bind((address, 53))
            tcp.bind((address, 53))
            tcp.listen(MAX_WORKERS)
            stage = "dns_nft_install"
            self.binder.install()
            refreshed_after = time.monotonic()
            stage = "dns_nft_heartbeat"
            self.binder.heartbeat()
            stage = "dns_health_write"
            self.mark_healthy(refreshed_after)
            thread = threading.Thread(target=self.heartbeat, daemon=True)
            thread.start()
            selector = selectors.DefaultSelector()
            selector.register(udp, selectors.EVENT_READ, "udp")
            selector.register(tcp, selectors.EVENT_READ, "tcp")
            stage = "dns_runtime"
            print(json.dumps({"aw_dns_ready": self.config["execution_id"]}), flush=True)
            while not self.dead.is_set():
                for key, _ in selector.select(timeout=0.1):
                    if key.data == "udp":
                        wire, peer = udp.recvfrom(MAX_WIRE + 1)
                        if self.slots.acquire(blocking=False):
                            threading.Thread(target=self.handle_udp, args=(udp, wire, peer), daemon=True).start()
                        else:
                            self.dead.set()
                    else:
                        conn, peer = tcp.accept()
                        if self.slots.acquire(blocking=False):
                            threading.Thread(target=self.handle_tcp, args=(conn, peer), daemon=True).start()
                        else:
                            conn.close()
                            self.dead.set()
        except Exception as exc:  # Emit only finite trusted diagnostics, then re-raise.
            self.fail(stage, exc)
            raise
        finally:
            self.dead.set()
            for resource in (udp, tcp, selector):
                if resource is not None:
                    resource.close()
            # This process never deletes the policy on death: empty health lease
            # leaves a fail-closed table until the owner cleans up after sandbox.
            if thread is not None:
                thread.join(timeout=1)
        raise PolicyError("execution DNS gateway stopped")

    def handle_udp(self, conn, wire, peer) -> None:
        try:
            result = self.process(wire, peer[0], protocol="udp", source_port=peer[1])
            if result is not None and not self.dead.is_set():
                conn.sendto(result, peer)
        except OSError:
            self.dead.set()
        finally:
            self.slots.release()

    @staticmethod
    def receive(conn, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            part = conn.recv(size - len(data))
            if not part:
                raise PolicyError("short TCP DNS frame")
            data.extend(part)
        return bytes(data)

    def handle_tcp(self, conn, peer) -> None:
        try:
            with conn:
                conn.settimeout(3)
                size = struct.unpack("!H", self.receive(conn, 2))[0]
                if not 12 <= size <= MAX_WIRE:
                    raise PolicyError("invalid TCP DNS size")
                result = self.process(self.receive(conn, size), peer[0], protocol="tcp", source_port=peer[1])
                if result is not None and not self.dead.is_set():
                    conn.sendall(struct.pack("!H", len(result)) + result)
        except (OSError, PolicyError):
            pass
        finally:
            self.slots.release()


def main() -> None:
    config = {}
    mediator = None
    try:
        if len(sys.argv) != 2 or len(sys.argv[1]) > 131072:
            raise PolicyError("invalid gateway configuration")
        config = json.loads(base64.b64decode(sys.argv[1], validate=True))
        if not isinstance(config, dict):
            raise PolicyError("invalid gateway configuration")
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        mediator = Mediator(config)
        mediator.serve()
    except Exception as exc:  # noqa: BLE001 - never print raw tracebacks/config/rules
        if mediator is not None:
            mediator.fail("dns_runtime", exc)
        elif isinstance(config, dict):
            report_failure(config, "dns_startup", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
