"""Kernel-enforced egress policy for one sandbox network.

The worker never interpolates user input into a shell command.  Every operation is
an argv list executed by a short-lived, fixed network-admin image.  The image is
only a transport for iptables/conntrack; policy decisions and rule construction
remain in this module so they can be tested without Linux privileges.

Domain rules are intentionally rejected here.  A DNS name is not a stable kernel
match and wildcard DNS cannot be safely translated to a finite IP set at runtime.
Rejecting the execution is fail-closed; silently accepting such a rule would make
restricted mode bypassable.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from docker.errors import APIError, DockerException
from docker.types import LogConfig

from app.core.config import settings
from app.executors.egress import RuleView, evaluate

logger = logging.getLogger(__name__)

NET_IMAGE = os.environ.get("AW_SBX_NET_IMAGE", "aw-sbx-net:1")
EGRESS_DNS_PROXY = settings.egress_dns_proxy


class NetworkPolicyError(RuntimeError):
    """A policy could not be installed safely; the sandbox must not start."""


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_PACKET_RE = re.compile(r"\bIP6?\s+(\S+)\.(\d+)\s+>\s+(\S+)\.(\d+):\s+(.*)")
_FIELD_RE = re.compile(
    r"src=(?P<src>[^\s]+)\s+dst=(?P<dst>[^\s]+)\s+"
    r"sport=(?P<sport>\d+)\s+dport=(?P<dport>\d+)"
    r"(?:\s+packets=\d+\s+bytes=(?P<bytes>\d+))?"
)


def _rule_view(value: RuleView | dict[str, Any]) -> RuleView:
    if isinstance(value, RuleView):
        return value
    try:
        return RuleView(
            id=value.get("id"), action=value["action"], target_type=value["target_type"],
            target=value["target"], port_range=value.get("port_range"),
        )
    except (AttributeError, KeyError, TypeError) as exc:
        raise NetworkPolicyError("invalid egress rule snapshot") from exc


def validate_execution_id(execution_id: str) -> str:
    """Validate the immutable runtime tag used by privileged helper commands."""
    if not isinstance(execution_id, str) or not _UUID_RE.fullmatch(execution_id):
        raise NetworkPolicyError("execution_id must be a UUID before network policy install")
    return execution_id


def _chain_names(execution_id: str) -> tuple[str, str]:
    token = validate_execution_id(execution_id).replace("-", "").upper()
    # iptables chain names are short and must not contain user-controlled syntax.
    return f"AWF{token[:20]}", f"AWI{token[:20]}"


def _port_args(rule: RuleView) -> list[str]:
    if not rule.port_range:
        return []
    value = rule.port_range.strip()
    if not re.fullmatch(r"\d{1,5}(?:-\d{1,5})?", value):
        raise NetworkPolicyError(f"unsupported port range: {value}")
    parts = value.split("-", 1)
    low = int(parts[0])
    high = int(parts[1]) if len(parts) == 2 else None
    if low < 1 or low > 65535 or (high is not None and not 1 <= high <= 65535):
        raise NetworkPolicyError(f"unsupported port range: {value}")
    return ["--dport", value.replace("-", ":")]


def _rule_target(rule: RuleView) -> tuple[str, int]:
    if rule.target_type == "domain":
        raise NetworkPolicyError(
            f"domain egress rule {rule.target!r} is unsupported by kernel runtime policy; "
            "use an IP/CIDR rule or fail closed"
        )
    try:
        if rule.target_type == "ip":
            ip = ipaddress.ip_address(rule.target.strip())
            return str(ip), ip.version
        network = ipaddress.ip_network(rule.target.strip(), strict=False)
        return str(network), network.version
    except ValueError as exc:
        raise NetworkPolicyError(f"invalid egress target: {rule.target}") from exc


def _family_commands(
    *,
    executable: str,
    bridge: str,
    dns_proxy_ip: str,
    forward_chain: str,
    input_chain: str,
    mode: Literal["default", "restricted"],
    global_rules: list[RuleView],
    agent_rules: list[RuleView],
    builtin_blacklist: list[str],
) -> list[list[str]]:
    try:
        dns_version = ipaddress.ip_address(dns_proxy_ip).version
    except ValueError as exc:
        raise NetworkPolicyError(f"DNS proxy address is not an IP: {dns_proxy_ip}") from exc
    family = 6 if executable == "ip6tables" else 4
    if dns_version != family:
        # No DNS ACCEPT rule in this family; the normal DNS DROP rule still applies.
        dns_args: list[str] = []
    else:
        dns_args = ["-d", dns_proxy_ip]

    commands: list[list[str]] = [
        [executable, "-N", forward_chain],
        [executable, "-N", input_chain],
    ]
    # DNS must precede the RFC1918/link-local drops because the proxy normally has
    # a private bridge address.  All other port-53 destinations are denied.
    if dns_args:
        for proto in ("udp", "tcp"):
            commands.append(
                [executable, "-A", forward_chain, "-p", proto, "--dport", "53", *dns_args, "-j", "ACCEPT"]
            )
            # Same-bridge proxy replies traverse FORWARD with this input bridge
            # too. Permit only established DNS replies before private-IP drops.
            commands.append(
                [executable, "-A", forward_chain, "-s", dns_proxy_ip, "-p", proto,
                 "--sport", "53", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"]
            )
    for proto in ("udp", "tcp"):
        commands.append([executable, "-A", forward_chain, "-p", proto, "--dport", "53", "-j", "DROP"])

    targets: list[str] = []
    for raw in builtin_blacklist:
        try:
            network = ipaddress.ip_network(raw.strip(), strict=False)
        except ValueError as exc:
            raise NetworkPolicyError(f"invalid builtin blacklist target: {raw}") from exc
        if network.version == family:
            targets.append(str(network))
    # IPv6 private/link-local/loopback destinations are always protected even when
    # an old deployment has only the historical IPv4 setting.
    if family == 6:
        targets.extend(["::1/128", "fc00::/7", "fe80::/10"])
    for target in dict.fromkeys(targets):
        commands.append([executable, "-A", forward_chain, "-d", target, "-j", "DROP"])

    for rule in global_rules:
        if rule.action != "deny":
            # Global rules are deny-only by contract; ignore malformed allow rows.
            continue
        target, version = _rule_target(rule)
        if version != family:
            continue
        action = rule.action
        target_args = ["-d", target]
        port_args = _port_args(rule)
        if port_args:
            for proto in ("tcp", "udp"):
                commands.append(
                    [executable, "-A", forward_chain, *target_args, "-p", proto, *port_args, "-j", "DROP" if action == "deny" else "ACCEPT"]
                )
        else:
            commands.append(
                [executable, "-A", forward_chain, *target_args, "-j", "DROP" if action == "deny" else "ACCEPT"]
            )

    if mode == "restricted":
        for rule in agent_rules:
            if rule.action != "allow":
                continue
            target, version = _rule_target(rule)
            if version != family:
                continue
            target_args = ["-d", target]
            port_args = _port_args(rule)
            if port_args:
                for proto in ("tcp", "udp"):
                    commands.append([executable, "-A", forward_chain, *target_args, "-p", proto, *port_args, "-j", "ACCEPT"])
            else:
                commands.append([executable, "-A", forward_chain, *target_args, "-j", "ACCEPT"])
        # No implicit allow in restricted mode.
        commands.append([executable, "-A", forward_chain, "-j", "DROP"])
    else:
        commands.append([executable, "-A", forward_chain, "-j", "ACCEPT"])

    # INPUT is separate from FORWARD: a bridge peer must not reach host services.
    # IPv6 nevertheless needs on-link neighbor discovery for its gateway. Without
    # this narrow control-plane exception TCP never reaches FORWARD at all.
    if family == 6:
        for kind in ("neighbor-solicitation", "neighbor-advertisement"):
            commands.append([executable, "-A", input_chain, "-p", "ipv6-icmp",
                             "--icmpv6-type", kind, "-m", "hl", "--hl-eq", "255", "-j", "ACCEPT"])
    commands.append([executable, "-A", input_chain, "-j", "DROP"])
    commands.append([executable, "-I", "FORWARD", "-i", bridge, "-j", forward_chain])
    commands.append([executable, "-I", "INPUT", "-i", bridge, "-j", input_chain])
    return commands


def compile_iptables_commands(
    *,
    bridge: str,
    dns_proxy_ip: str,
    mode: Literal["default", "restricted"],
    global_rules: list[RuleView],
    agent_rules: list[RuleView],
    builtin_blacklist: list[str],
) -> list[list[str]]:
    """Compile an isolated v4/v6 policy without shell interpolation."""
    if not re.fullmatch(r"br-[0-9a-fA-F]{6,64}", bridge):
        raise NetworkPolicyError("bridge interface is not a Docker bridge id")
    forward, input_chain = _chain_names(str(uuid.uuid4()))
    # The generated chain names are replaced by the caller to keep this pure helper
    # convenient for tests; runtime uses `compile_runtime_commands` below.
    return _compile_with_chains(
        bridge=bridge, dns_proxy_ip=dns_proxy_ip, mode=mode,
        global_rules=global_rules, agent_rules=agent_rules,
        builtin_blacklist=builtin_blacklist, forward=forward, input_chain=input_chain,
    )


def _compile_with_chains(
    *, bridge: str, dns_proxy_ip: str, mode: Literal["default", "restricted"],
    global_rules: list[RuleView], agent_rules: list[RuleView], builtin_blacklist: list[str],
    forward: str, input_chain: str,
) -> list[list[str]]:
    commands: list[list[str]] = []
    commands.extend(_family_commands(
        executable="iptables", bridge=bridge, dns_proxy_ip=dns_proxy_ip,
        forward_chain=forward, input_chain=input_chain, mode=mode,
        global_rules=global_rules, agent_rules=agent_rules, builtin_blacklist=builtin_blacklist,
    ))
    commands.extend(_family_commands(
        executable="ip6tables", bridge=bridge, dns_proxy_ip=dns_proxy_ip,
        forward_chain=forward + "6", input_chain=input_chain + "6", mode=mode,
        global_rules=global_rules, agent_rules=agent_rules, builtin_blacklist=builtin_blacklist,
    ))
    return commands


def compile_runtime_commands(
    *, execution_id: str, bridge: str, dns_proxy_ip: str, mode: Literal["default", "restricted"],
    global_rules: list[RuleView], agent_rules: list[RuleView], builtin_blacklist: list[str],
) -> tuple[list[list[str]], list[list[str]]]:
    forward, input_chain = _chain_names(execution_id)
    install = _compile_with_chains(
        bridge=bridge, dns_proxy_ip=dns_proxy_ip, mode=mode,
        global_rules=global_rules, agent_rules=agent_rules,
        builtin_blacklist=builtin_blacklist, forward=forward, input_chain=input_chain,
    )
    cleanup = []
    for executable, suffix in (("iptables", ""), ("ip6tables", "6")):
        fwd, inp = forward + suffix, input_chain + suffix
        cleanup.extend([
            [executable, "-D", "FORWARD", "-i", bridge, "-j", fwd],
            [executable, "-D", "INPUT", "-i", bridge, "-j", inp],
            [executable, "-F", fwd], [executable, "-X", fwd],
            [executable, "-F", inp], [executable, "-X", inp],
        ])
    return install, cleanup


def _connection_record(
    source_ip: str, source_port: int, target_ip: str, port: int, protocol: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    record = {
        "source_ip": source_ip, "source_port": source_port,
        "target_ip": target_ip, "domain": "ip-direct", "port": port,
        "protocol": protocol, "action": "allow", "matched_rule_id": None,
        "bytes_out": None,
    }
    if timestamp is not None:
        try:
            record["occurred_at"] = datetime.fromtimestamp(float(timestamp), UTC).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return record


def parse_conntrack_output(output: str | bytes, *, source_ip: str) -> list[dict[str, Any]]:
    """Keep original-direction five-tuples and conntrack lifetimes, not targets.

    UPDATE/DESTROY counters update the corresponding record. A fresh NEW event
    (or a different conntrack id) starts a new record even if the tuple is reused.
    A snapshot without event/id evidence cannot disambiguate tuple reuse.
    """
    text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
    records: list[dict[str, Any]] = []
    source_ip = str(ipaddress.ip_address(source_ip))
    current: dict[tuple[str, str, int, str, int, str | None], dict[str, Any]] = {}
    finished: set[tuple[str, str, int, str, int, str | None]] = set()
    for line in text.splitlines():
        protocol_match = re.search(r"\b(tcp|udp)(?:\s|$)", line)
        match = _FIELD_RE.search(line)
        # Only the FIRST tuple establishes initiation direction. An inbound
        # connection's reply tuple also has src=sandbox and must not be counted.
        if not protocol_match or not match:
            continue
        try:
            source = str(ipaddress.ip_address(match.group("src")))
            target = str(ipaddress.ip_address(match.group("dst")))
        except ValueError:
            continue
        if source != source_ip:
            continue
        protocol, port = protocol_match.group(1), int(match.group("dport"))
        source_port = int(match.group("sport"))
        id_match = re.search(r"\bid=(\d+)\b", line)
        identity = id_match.group(1) if id_match else None
        key = (protocol, source_ip, source_port, target, port, identity)
        record = current.get(key)
        is_new = "[NEW]" in line
        if record is None or (is_new and (identity is None or key in finished)):
            record = _connection_record(source_ip, source_port, target, port, protocol)
            records.append(record)
            current[key] = record
            finished.discard(key)
        if match.group("bytes"):
            record["bytes_out"] = max(record["bytes_out"] or 0, int(match.group("bytes")))
        if "[DESTROY]" in line:
            finished.add(key)
    return records


def parse_packet_output(output: str | bytes, *, source_ip: str) -> list[dict[str, Any]]:
    """Observe outbound TCP connection attempts / UDP five-tuple flows.

    TCP SYN retransmissions retain one fact; a fresh absolute initial sequence
    or a SYN after closure starts another, including reuse of the same source
    port. UDP has no handshake: same-five-tuple socket reuse is not distinguishable
    from more datagrams on one flow. ICMP/fragments/non-port traffic are not
    connection facts in this parser. No payload or byte-count estimate is stored.
    """
    text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
    records: list[dict[str, Any]] = []
    flows: dict[tuple[str, int, str, int], dict[str, Any]] = {}
    source_ip = str(ipaddress.ip_address(source_ip))
    for line in text.splitlines():
        match = _PACKET_RE.search(line)
        if not match:
            continue
        try:
            source = str(ipaddress.ip_address(match.group(1)))
            target = str(ipaddress.ip_address(match.group(3)))
        except ValueError:
            continue
        if source != source_ip:
            continue
        port = int(match.group(4))
        source_port, detail = int(match.group(2)), match.group(5)
        flags_match = re.match(r"Flags \[([^]]*)\]", detail)
        protocol = "tcp" if flags_match else "udp"
        key = (protocol, source_port, target, port)
        flow = flows.get(key)
        flags = flags_match.group(1) if flags_match else ""
        initial_syn = "S" in flags and "." not in flags
        seq_match = re.search(r"\bseq (\d+)", detail) if initial_syn else None
        sequence = seq_match.group(1) if seq_match else None
        if flow is None or (initial_syn and (flow["closed"] or flow["sequence"] != sequence)):
            timestamp_match = re.match(r"(\d+(?:\.\d+)?)\s", line)
            record = _connection_record(
                source_ip, source_port, target, port, protocol,
                timestamp_match.group(1) if timestamp_match else None,
            )
            records.append(record)
            flow = {"sequence": sequence, "closed": False}
            flows[key] = flow
        if "R" in flags or "F" in flags:
            flow["closed"] = True
    return records


def _dns_ip_domains(logs: bytes | str, source_ip: str) -> dict[str, str]:
    """Map an IP to a domain only when query/reply evidence is unambiguous.

    dnsmasq reply records do not carry the original client IP. We therefore only
    attribute an IP when exactly one domain queried by this sandbox returned it,
    and no other queried domain returned the same address during the window.
    """
    text = logs.decode("utf-8", "replace") if isinstance(logs, bytes) else logs
    query_re = re.compile(r"query\[[^]]+\]\s+(\S+)\s+from\s+" + re.escape(source_ip) + r"(?:\s|$)")
    reply_re = re.compile(r"(?:reply|cached)\s+(\S+)\s+is\s+(\S+)")
    queried: set[str] = set()
    answers: dict[str, set[str]] = {}
    for line in text.splitlines():
        query = query_re.search(line)
        if query:
            queried.add(query.group(1).rstrip(".").lower())
        reply = reply_re.search(line)
        if reply:
            domain = reply.group(1).rstrip(".").lower()
            try:
                ip = str(ipaddress.ip_address(reply.group(2)))
            except ValueError:
                continue
            answers.setdefault(ip, set()).add(domain)
    return {
        ip: next(iter(domains))
        for ip, domains in answers.items()
        if len(domains) == 1 and domains.issubset(queried)
    }


@dataclass
class NetworkCapture:
    """Per-execution policy and pre-firewall packet watcher lifecycle."""

    client: Any
    network: Any
    execution_id: str
    mode: Literal["default", "restricted"]
    global_rules: list[RuleView]
    agent_rules: list[RuleView]
    builtin_blacklist: list[str]
    dns_proxy: Any
    dns_proxy_ip: str
    bridge: str
    install_commands: list[list[str]]
    cleanup_commands: list[list[str]]
    watcher: Any = None
    source_ip: str | None = None
    source_ipv6: str | None = None
    started_at: int = 0

    @classmethod
    def prepare(cls, client: Any, network: Any, execution_id: str, snapshot: dict[str, Any] | None) -> NetworkCapture:
        validate_execution_id(execution_id)
        snapshot = snapshot or {}
        mode = snapshot.get("mode", "default")
        if mode not in ("default", "restricted"):
            raise NetworkPolicyError(f"unsupported egress mode: {mode}")
        global_rules = [_rule_view(item) for item in snapshot.get("global_rules", [])]
        agent_rules = [_rule_view(item) for item in snapshot.get("agent_rules", [])]
        builtins = list(snapshot.get("builtin_blacklist", settings.egress_builtin_blacklist_list))
        if not hasattr(client.containers, "get"):
            raise NetworkPolicyError("Docker client lacks container lookup for DNS proxy")
        try:
            dns_proxy = client.containers.get(EGRESS_DNS_PROXY)
            network.connect(dns_proxy)
            dns_proxy.reload()
            address = (getattr(dns_proxy, "attrs", {}) or {}).get("NetworkSettings", {}).get("Networks", {}).get(network.name, {})
            dns_ip = address.get("IPAddress")
            if not dns_ip:
                raise NetworkPolicyError("DNS proxy has no address on sandbox network")
            attrs = getattr(network, "attrs", {}) or {}
            network_id = attrs.get("Id") or attrs.get("ID")
            bridge = attrs.get("Options", {}).get("com.docker.network.bridge.name") or (
                f"br-{str(network_id)[:12]}" if network_id else ""
            )
            if not bridge:
                raise NetworkPolicyError("Docker network has no bridge interface")
            install, cleanup = compile_runtime_commands(
                execution_id=execution_id, bridge=bridge, dns_proxy_ip=dns_ip,
                mode=mode, global_rules=global_rules, agent_rules=agent_rules,
                builtin_blacklist=builtins,
            )
            # Reserve an address in this execution-private network at create time.
            # Docker does not populate IPAddress until start; capture must be ready
            # before user code, so dynamic allocation cannot be used here. Docker
            # rejects an occupied/out-of-subnet address rather than running open.
            source_ip = str(ipaddress.ip_address(dns_ip) + 1)
            dns_ipv6 = address.get("GlobalIPv6Address")
            source_ipv6 = str(ipaddress.IPv6Address(dns_ipv6) + 1) if dns_ipv6 else None
            if attrs.get("EnableIPv6") and source_ipv6 is None:
                raise NetworkPolicyError("IPv6-enabled network has no observable DNS endpoint for source reservation")
            obj = cls(client, network, execution_id, mode, global_rules, agent_rules, builtins,
                      dns_proxy, dns_ip, bridge, install, cleanup,
                      source_ip=source_ip, source_ipv6=source_ipv6)
            try:
                obj._run_commands(install)
            except Exception:
                for command in cleanup:
                    try:
                        obj._run_command(command)
                    except NetworkPolicyError:
                        pass
                raise
            return obj
        except Exception as exc:
            try:
                if "dns_proxy" in locals():
                    network.disconnect(dns_proxy, force=True)
            except (APIError, DockerException, OSError, RuntimeError) as cleanup_exc:
                logger.debug("network policy setup cleanup failed: %s", cleanup_exc)
            if isinstance(exc, NetworkPolicyError):
                raise
            raise NetworkPolicyError("failed to install sandbox network policy") from exc

    def _run_command(self, command: list[str]) -> bytes:
        try:
            result = self.client.containers.run(
                NET_IMAGE, command=command, network_mode="host", cap_add=["NET_ADMIN"],
                detach=False, remove=True, labels={"aw.managed": "true", "aw.execution": self.execution_id},
            )
            return result if isinstance(result, bytes) else bytes(result or b"")
        except (APIError, DockerException, OSError) as exc:
            raise NetworkPolicyError(f"network policy command failed: {command[0]}") from exc

    def _run_commands(self, commands: Iterable[list[str]]) -> None:
        for command in commands:
            self._run_command(command)

    def start_capture(self, container: Any) -> None:
        """Wait for header capture readiness before the user process starts."""
        attrs = getattr(container, "attrs", {}) or {}
        networks = (attrs.get("NetworkSettings", {}) or {}).get("Networks", {})
        network_info: dict[str, Any] = networks.get(self.network.name) or next(iter(networks.values()), {})
        address = network_info.get("IPAddress") or (network_info.get("IPAMConfig") or {}).get("IPv4Address")
        if not self.source_ip or address != self.source_ip:
            raise NetworkPolicyError("sandbox container has no matching reserved network address")
        ipv6_address = network_info.get("GlobalIPv6Address") or (network_info.get("IPAMConfig") or {}).get("IPv6Address")
        network_attrs = getattr(self.network, "attrs", {}) or {}
        if (network_attrs.get("EnableIPv6") or self.source_ipv6) and not ipv6_address:
            # Docker may only assign dynamic v6 on start, which is too late for
            # the pre-user-code capture contract. Require a reserved endpoint.
            raise NetworkPolicyError("IPv6-enabled network requires a known prestart sandbox IPv6 address")
        if ipv6_address:
            try:
                actual_ipv6 = str(ipaddress.IPv6Address(ipv6_address))
                if self.source_ipv6 and actual_ipv6 != self.source_ipv6:
                    raise NetworkPolicyError("sandbox container has no matching reserved IPv6 address")
                self.source_ipv6 = actual_ipv6
            except ipaddress.AddressValueError as exc:
                raise NetworkPolicyError("sandbox IPv6 address is invalid") from exc
        sources = ["src", "host", self.source_ip]
        if self.source_ipv6:
            sources = ["(", *sources, "or", "src", "host", self.source_ipv6, ")"]
        self.started_at = int(datetime.now(UTC).timestamp())
        try:
            self.watcher = self.client.containers.run(
                NET_IMAGE,
                command=["tcpdump", "--immediate-mode", "-l", "-nn", "-S", "-tt", "-i", self.bridge,
                         *sources, "and", "(tcp or udp)"],
                network_mode="host", cap_add=["NET_ADMIN", "NET_RAW"], detach=True, remove=False,
                name=f"aw-egress-{self.execution_id}",
                labels={"aw.managed": "true", "aw.execution": self.execution_id},
                read_only=True, cap_drop=["ALL"], security_opt=["no-new-privileges"],
                mem_limit="64m", pids_limit=16,
                log_config=LogConfig(type="local", config={"max-size": "4m", "max-file": "1", "compress": "false"}),
            )
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if b"listening on" in self.watcher.logs(stdout=False, stderr=True):
                    break
                time.sleep(0.02)
            else:
                raise NetworkPolicyError("packet capture did not become ready")
        except (APIError, DockerException, OSError) as exc:
            raise NetworkPolicyError("unable to start packet capture") from exc

    def collect(self) -> tuple[list[dict[str, Any]], bool]:
        if self.watcher is None or not self.source_ip:
            return [], False
        try:
            try:
                self.watcher.stop(timeout=3)
            except TypeError:
                self.watcher.stop()
            status = self.watcher.wait(timeout=3)
            stderr = self.watcher.logs(stdout=False, stderr=True)
            diagnostics = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
            drops = re.findall(r"(\d+) packets? dropped by (?:kernel|interface)", diagnostics)
            # A killed/OOM'd watcher or missing final statistics cannot prove a
            # complete audit. Never return a misleading empty/successful capture.
            if not isinstance(status, dict) or status.get("StatusCode") != 0:
                raise NetworkPolicyError("packet capture exited unsuccessfully")
            if not re.search(r"\d+ packets? dropped by kernel", diagnostics) or any(int(n) for n in drops):
                raise NetworkPolicyError("packet capture is incomplete (missing statistics or dropped packets)")
            events = self.watcher.logs(stdout=True, stderr=False)
            packet_text = events.decode("utf-8", "replace") if isinstance(events, bytes) else events
            captured = re.search(r"(\d+) packets? captured", diagnostics)
            if captured is None or int(captured.group(1)) != len(_PACKET_RE.findall(packet_text)):
                raise NetworkPolicyError("packet capture log is truncated or contains unparseable packets")
            records = parse_packet_output(packet_text, source_ip=self.source_ip)
            if self.source_ipv6:
                records.extend(parse_packet_output(events, source_ip=self.source_ipv6))
            records.sort(key=lambda record: record.get("occurred_at", ""))
            dns_logs = self.dns_proxy.logs(since=self.started_at)
            ip_domains = _dns_ip_domains(dns_logs, self.source_ip)
            for record in records:
                record["domain"] = ip_domains.get(record["target_ip"], "ip-direct")
            for record in records:
                if record["target_ip"] == self.dns_proxy_ip and record["port"] == 53:
                    record.update(action="allow", matched_rule_id=None, domain="dns-proxy")
                    record.setdefault("occurred_at", datetime.now(UTC).isoformat())
                    continue
                decision = evaluate(
                    record["target_ip"], record.get("domain"), record["port"],
                    mode=self.mode, global_rules=self.global_rules,
                    agent_rules=self.agent_rules,
                    # Keep post-run classification aligned with the v6 rules that
                    # are always installed even when the legacy snapshot is v4-only.
                    builtin_blacklist=[
                        *self.builtin_blacklist, "::1/128", "fc00::/7", "fe80::/10"
                    ],
                )
                record["action"] = decision.action
                record["matched_rule_id"] = decision.matched_rule_id
                record.setdefault("occurred_at", datetime.now(UTC).isoformat())
            denied = any(record["action"] == "deny" for record in records)
            return records, denied
        finally:
            try:
                self.watcher.remove(force=True)
            except (APIError, DockerException, OSError, RuntimeError) as cleanup_exc:
                logger.debug("packet watcher cleanup failed: %s", cleanup_exc)
            self.watcher = None

    def cleanup(self) -> None:
        if self.watcher is not None:
            try:
                self.watcher.stop(timeout=3)
            except (APIError, DockerException, OSError, RuntimeError) as cleanup_exc:
                logger.debug("packet watcher stop failed: %s", cleanup_exc)
            try:
                self.watcher.remove(force=True)
            except (APIError, DockerException, OSError, RuntimeError) as cleanup_exc:
                logger.debug("packet watcher remove failed: %s", cleanup_exc)
            self.watcher = None
        for command in self.cleanup_commands:
            try:
                self._run_command(command)
            except NetworkPolicyError:
                # Cleanup is best effort and must never flush global host rules.
                pass
        try:
            self.network.disconnect(self.dns_proxy, force=True)
        except (APIError, DockerException, OSError, RuntimeError) as cleanup_exc:
            logger.debug("DNS proxy detach failed: %s", cleanup_exc)
