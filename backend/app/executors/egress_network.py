"""Kernel-enforced egress policy for one sandbox network.

The worker never interpolates user input into a shell command. Privileged work
uses fixed, resource-bounded helpers with argv/data-only inputs. Base IP/CIDR
ACL construction is here; the trusted, pure domain-policy implementation and
header-only observer live in deploy/sbx-net and are tested without privileges.

Domain snapshots use an execution-private trusted DNS mediator. It validates
complete alias chains and atomically binds answers to timeout-enforced nft sets
BEFORE replying. Shared DNS logs are never an authorization source. A kernel
health lease expires within one second of gateway death, including established
flows. This is destination authorization, not TLS/SNI identity verification.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from docker.errors import APIError, ContainerError, DockerException
from docker.types import LogConfig

from app.core.config import settings
from app.executors.egress import RuleView, evaluate

logger = logging.getLogger(__name__)

NET_IMAGE = os.environ.get("AW_SBX_NET_IMAGE", "aw-sbx-net:1")
EGRESS_DNS_PROXY = settings.egress_dns_proxy

# Kept identical to the standalone helper's wire schema; checked by unit tests.
_DNS_DIAGNOSTIC_STAGES = frozenset({
    "dns_startup", "dns_socket_bind", "dns_nft_install", "dns_nft_heartbeat",
    "dns_health_write", "dns_runtime", "dns_health_check", "dns_audit", "packet_audit",
})
_DNS_DIAGNOSTIC_CODES = frozenset({
    "unknown", "permission_denied", "address_in_use", "address_unavailable",
    "missing_executable", "invalid_argument", "no_space_left", "operation_timeout",
    "nft_syntax", "nft_feature_unavailable", "nft_transaction_failed", "helper_exited",
    "helper_not_ready", "health_expired", "helper_paused", "helper_oom", "diagnostic_invalid",
    "capture_exited", "capture_incomplete", "capture_truncated",
})
_NETWORK_DIAGNOSTIC_STAGES = frozenset({"policy_install"})
_NETWORK_DIAGNOSTIC_CODES = frozenset({
    "permission_denied", "address_in_use", "address_unavailable", "missing_executable",
    "invalid_argument", "no_space_left", "operation_timeout", "nft_syntax",
    "nft_feature_unavailable", "nft_transaction_failed",
})


class NetworkPolicyError(RuntimeError):
    """A policy could not be installed safely; the sandbox must not start."""


def finite_error_token(failure: BaseException) -> str | None:
    """Return only a canonical DNS diagnostic, never exception text."""
    if not isinstance(failure, NetworkPolicyError):
        return None
    match = re.fullmatch(r"dns_failure\[([a-z_]{1,40}),([a-z_]{1,40})\]", str(failure)[:128])
    if match and match[1] in _DNS_DIAGNOSTIC_STAGES and match[2] in _DNS_DIAGNOSTIC_CODES:
        return f"dns_failure[{match[1]},{match[2]}]"
    return None


def _finite_network_failure(output: Any, execution_id: str) -> str | None:
    """Decode one trusted helper event without exposing helper output."""
    if not isinstance(output, (bytes, str)):
        try:
            chunks: list[bytes] = []
            size = 0
            for chunk in output:
                if not isinstance(chunk, (bytes, str)):
                    return None
                encoded = chunk.encode() if isinstance(chunk, str) else chunk
                size += len(encoded)
                if size > 131072:
                    return None
                chunks.append(encoded)
            output = b"".join(chunks)
        except (OSError, TypeError, ValueError):
            return None
    if len(output) > 131072:
        return None
    text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if (isinstance(event, dict) and event.get("aw_net_failure") == 1
                and event.get("execution_id") == execution_id
                and event.get("stage") in _NETWORK_DIAGNOSTIC_STAGES
                and event.get("code") in _NETWORK_DIAGNOSTIC_CODES
                and set(event) == {"aw_net_failure", "execution_id", "stage", "code"}):
            return f"network_failure[{event['stage']},{event['code']}]"
    return None


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_PACKET_RE = re.compile(r"\bIP6?\s+(\S+)\.(\d+)\s+>\s+(\S+)\.(\d+):\s+(.*)")
_IP_HEADER_RE = re.compile(r"\bIP6?\s+(\S+)\s+>\s+(\S+):\s+(.*)")
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


def _protocol_guard(execution_id: str, bridge: str) -> str:
    """Fail closed on protocols/fragment/extension paths not fully audited.

    Their pre-firewall IP headers are still captured as port=0 denied facts.
    This guard applies even in ordinary default mode, not just domain mode.
    """
    token = uuid.UUID(validate_execution_id(execution_id)).hex
    if not re.fullmatch(r"br-[0-9a-fA-F]{6,64}", bridge):
        raise NetworkPolicyError("invalid bridge for protocol guard")
    prefix = f'iifname "{bridge}" '
    rows = [prefix + "ip frag-off & 0x3fff != 0 drop",
            prefix + "meta nfproto ipv6 ip6 nexthdr != { tcp, udp } drop",
            prefix + "meta l4proto != { tcp, udp } drop"]
    # PREROUTING must precede conntrack defragmentation (-400). A filter-only
    # fragment check is too late: Linux reassembles the datagram before FORWARD.
    # IPv6 ICMP is permitted here for host neighbor discovery, but unsupported
    # protocols (including routed ICMP) are still denied in the FORWARD chain.
    early = [prefix + "ip frag-off & 0x3fff != 0 drop",
             prefix + "meta nfproto ipv6 ip6 nexthdr != { tcp, udp, ipv6-icmp } drop"]
    return (f"table inet awp_{token} {{\nchain pre {{ type filter hook prerouting "
            "priority -450; policy accept; " + "; ".join(early) + "; }\n"
            "chain forward { type filter hook forward priority -20; policy accept; "
            + "; ".join(rows) + "; }\n}\n")


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
    host_dns_upstream: str | None = None,
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

    if host_dns_upstream is not None and dns_args:
        upstream = str(ipaddress.ip_address(host_dns_upstream))
        for proto in ("udp", "tcp"):
            # Only the execution gateway's DNS listener and its established
            # upstream replies are reachable on the host. No other host service.
            commands.append([executable, "-A", input_chain, "-d", dns_proxy_ip,
                             "-p", proto, "--dport", "53", "-j", "ACCEPT"])
            commands.append([executable, "-A", input_chain, "-s", upstream,
                             "-p", proto, "--sport", "53", "-m", "conntrack",
                             "--ctstate", "ESTABLISHED", "-j", "ACCEPT"])

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
    forward: str, input_chain: str, host_dns_upstream: str | None = None,
) -> list[list[str]]:
    commands: list[list[str]] = []
    commands.extend(_family_commands(
        executable="iptables", bridge=bridge, dns_proxy_ip=dns_proxy_ip,
        forward_chain=forward, input_chain=input_chain, mode=mode,
        global_rules=global_rules, agent_rules=agent_rules, builtin_blacklist=builtin_blacklist,
        host_dns_upstream=host_dns_upstream,
    ))
    commands.extend(_family_commands(
        executable="ip6tables", bridge=bridge, dns_proxy_ip=dns_proxy_ip,
        forward_chain=forward + "6", input_chain=input_chain + "6", mode=mode,
        global_rules=global_rules, agent_rules=agent_rules, builtin_blacklist=builtin_blacklist,
        host_dns_upstream=host_dns_upstream,
    ))
    return commands


def compile_runtime_commands(
    *, execution_id: str, bridge: str, dns_proxy_ip: str, mode: Literal["default", "restricted"],
    global_rules: list[RuleView], agent_rules: list[RuleView], builtin_blacklist: list[str],
    host_dns_upstream: str | None = None,
) -> tuple[list[list[str]], list[list[str]]]:
    forward, input_chain = _chain_names(execution_id)
    install = _compile_with_chains(
        bridge=bridge, dns_proxy_ip=dns_proxy_ip, mode=mode,
        global_rules=global_rules, agent_rules=agent_rules,
        builtin_blacklist=builtin_blacklist, forward=forward, input_chain=input_chain,
        host_dns_upstream=host_dns_upstream,
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
    from more datagrams on one flow. Unsupported IP protocols/fragments/extension
    paths are denied port=0 target facts, not silently omitted or permitted.
    No payload or byte-count estimate is stored.
    """
    text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
    records: list[dict[str, Any]] = []
    flows: dict[tuple[str, int, str, int], dict[str, Any]] = {}
    source_ip = str(ipaddress.ip_address(source_ip))
    for line in text.splitlines():
        match = _PACKET_RE.search(line)
        if match:
            try:
                ipaddress.ip_address(match.group(1))
                ipaddress.ip_address(match.group(3))
            except ValueError:
                # A portless IPv4 address's last octet is NOT a port.
                match = None
        if not match:
            header = _IP_HEADER_RE.search(line)
            if not header:
                continue
            try:
                source = str(ipaddress.ip_address(header.group(1)))
                target = str(ipaddress.ip_address(header.group(2)))
            except ValueError:
                continue
            if source != source_ip:
                continue
            detail = header.group(3)
            reason = detail.removeprefix("AW-DENY ").split(" ")[0]
            protocol = reason if detail.startswith("AW-DENY ") else (
                "icmp6" if detail.startswith("ICMP6") else "icmp" if detail.startswith("ICMP") else "ip-other"
            )
            timestamp_match = re.match(r"(\d+(?:\.\d+)?)\s", line)
            record = _connection_record(source, 0, target, 0, protocol[:10],
                                        timestamp_match.group(1) if timestamp_match else None)
            record.update(action="deny", _kernel_denied=True)
            records.append(record)
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
    domain_gateway: Any = None
    domain_config: dict[str, Any] | None = None

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
            mediated = any(r.target_type == "domain" and r.action == "deny" for r in global_rules) or (
                mode == "restricted" and any(r.target_type == "domain" and r.action == "allow" for r in agent_rules)
            )
            upstream_ip = dns_ip
            if mediated:
                gateways = (attrs.get("IPAM") or {}).get("Config") or []
                dns_ip = next((g.get("Gateway") for g in gateways
                               if g.get("Gateway") and ipaddress.ip_address(g["Gateway"]).version == 4), None)
                if not dns_ip:
                    raise NetworkPolicyError("domain DNS mediator requires execution bridge IPv4 gateway")
            install, cleanup = compile_runtime_commands(
                execution_id=execution_id, bridge=bridge, dns_proxy_ip=dns_ip,
                mode="default" if mediated else mode,
                global_rules=[r for r in global_rules if r.target_type != "domain"] if mediated else global_rules,
                agent_rules=[] if mediated else agent_rules,
                builtin_blacklist=builtins, host_dns_upstream=upstream_ip if mediated else None,
            )
            cleanup.append(["nft", "delete", "table", "inet", "awp_" + uuid.UUID(execution_id).hex])
            if mediated:
                cleanup.append(["nft", "delete", "table", "inet", "awd_" + uuid.UUID(execution_id).hex])
            # Reserve an address in this execution-private network at create time.
            # Docker does not populate IPAddress until start; capture must be ready
            # before user code, so dynamic allocation cannot be used here. Docker
            # rejects an occupied/out-of-subnet address rather than running open.
            source_ip = str(ipaddress.ip_address(upstream_ip) + 1)
            dns_ipv6 = address.get("GlobalIPv6Address")
            source_ipv6 = str(ipaddress.IPv6Address(dns_ipv6) + 1) if dns_ipv6 else None
            if attrs.get("EnableIPv6") and source_ipv6 is None:
                raise NetworkPolicyError("IPv6-enabled network has no observable DNS endpoint for source reservation")
            obj = cls(client, network, execution_id, mode, global_rules, agent_rules, builtins,
                      dns_proxy, dns_ip, bridge, install, cleanup,
                      source_ip=source_ip, source_ipv6=source_ipv6)
            try:
                obj._run_commands(install, nft_script=_protocol_guard(execution_id, bridge))
                if mediated:
                    obj.domain_config = {
                        "execution_id": execution_id, "bridge": bridge, "mode": mode,
                        "listen_ip": dns_ip, "upstream_ip": upstream_ip,
                        "source_ip": source_ip, "source_ipv6": source_ipv6,
                        "global_rules": [asdict(r) for r in global_rules],
                        "agent_rules": [asdict(r) for r in agent_rules],
                        "builtin_blacklist": builtins,
                    }
                    obj._start_domain_gateway()
            except Exception:
                obj.cleanup()
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
                cap_drop=["ALL"], read_only=True, security_opt=["no-new-privileges"],
                mem_limit="64m", memswap_limit="64m", pids_limit=32,
            )
            return result if isinstance(result, bytes) else bytes(result or b"")
        except ContainerError as exc:
            token = _finite_network_failure(
                getattr(exc, "stderr", None) or getattr(exc, "output", None), self.execution_id,
            )
            if token:
                raise NetworkPolicyError(token) from exc
            raise NetworkPolicyError(f"network policy command failed: {command[0]}") from exc
        except (APIError, DockerException, OSError) as exc:
            raise NetworkPolicyError(f"network policy command failed: {command[0]}") from exc

    def _run_commands(
        self, commands: Iterable[list[str]], *, best_effort: bool = False, nft_script: str | None = None,
    ) -> None:
        # One fixed helper, not one Docker container PER firewall rule. The helper
        # executes bounded argv arrays, without shell parsing or interpolation.
        payload = base64.b64encode(json.dumps({
            "execution_id": self.execution_id, "commands": list(commands),
            "best_effort": best_effort, "nft_script": nft_script,
        }, separators=(",", ":")).encode()).decode()
        self._run_command(["python3", "/opt/aw/command_batch.py", payload])

    @staticmethod
    def _dns_error(stage: str, code: str) -> NetworkPolicyError:
        if stage not in _DNS_DIAGNOSTIC_STAGES or code not in _DNS_DIAGNOSTIC_CODES:
            stage, code = "dns_startup", "diagnostic_invalid"
        return NetworkPolicyError(f"dns_failure[{stage},{code}]")

    def _check_dns_failure(self, event: dict[str, Any]) -> None:
        if "aw_dns_failure" not in event:
            return
        stage, code = event.get("stage"), event.get("code")
        if (event.get("aw_dns_failure") != 1 or event.get("execution_id") != self.execution_id
                or not isinstance(stage, str) or not isinstance(code, str)
                or stage not in _DNS_DIAGNOSTIC_STAGES or code not in _DNS_DIAGNOSTIC_CODES
                or set(event) != {"aw_dns_failure", "execution_id", "stage", "code"}):
            raise self._dns_error("dns_startup", "diagnostic_invalid")
        raise self._dns_error(stage, code)

    def _gateway_records(self) -> list[dict[str, Any]]:
        # Trusted helper only, stdout only, bounded tail. Never propagate raw
        # helper logs or failed nft command output across the worker boundary.
        raw = self.domain_gateway.logs(tail=32, stdout=True, stderr=False)
        if not isinstance(raw, (bytes, str)) or len(raw) > 131072:
            raise self._dns_error("dns_startup", "diagnostic_invalid")
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            records = []
            for line in text.splitlines():
                if not line.startswith("{"):
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise self._dns_error("dns_startup", "diagnostic_invalid")
                self._check_dns_failure(event)
                records.append(event)
            return records
        except (ValueError, UnicodeError) as exc:
            raise self._dns_error("dns_startup", "diagnostic_invalid") from exc

    def _raise_gateway_failure(self, stage: str, code: str) -> None:
        self._gateway_records()  # A precise trusted helper cause takes priority.
        raise self._dns_error(stage, code)

    def _start_domain_gateway(self) -> None:
        payload = base64.b64encode(json.dumps(self.domain_config, separators=(",", ":")).encode()).decode()
        self.domain_gateway = self.client.containers.run(
            NET_IMAGE, command=["python3", "-u", "/opt/aw/dns_mediator.py", payload],
            network_mode="host", cap_drop=["ALL"], cap_add=["NET_ADMIN", "NET_BIND_SERVICE"],
            detach=True, remove=False, read_only=True, security_opt=["no-new-privileges"],
            name=f"aw-domain-{self.execution_id}", mem_limit="64m", memswap_limit="64m",
            nano_cpus=250_000_000, pids_limit=64,
            tmpfs={"/run": "rw,noexec,nosuid,nodev,size=1m"},
            labels={"aw.managed": "true", "aw.execution": self.execution_id},
            log_config=LogConfig(type="local", config={"max-size": "4m", "max-file": "1", "compress": "false"}),
        )
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            records = self._gateway_records()
            if any(event.get("aw_dns_ready") == self.execution_id for event in records):
                self.assert_healthy()
                return
            self.domain_gateway.reload()
            state = (getattr(self.domain_gateway, "attrs", {}) or {}).get("State") or {}
            if state.get("Running") is not True:
                code = "helper_oom" if state.get("OOMKilled") is True else "helper_exited"
                self._raise_gateway_failure("dns_startup", code)
            time.sleep(0.02)
        self._raise_gateway_failure("dns_startup", "helper_not_ready")

    def assert_healthy(self) -> None:
        """Worker-side liveness complements the independent <=1s kernel lease."""
        if self.domain_gateway is None:
            return
        try:
            self.domain_gateway.reload()
            state = (getattr(self.domain_gateway, "attrs", {}) or {}).get("State") or {}
            if state.get("Running") is not True or state.get("Paused") is True:
                code = ("helper_paused" if state.get("Paused") is True else
                        "helper_oom" if state.get("OOMKilled") is True else "helper_exited")
                self._raise_gateway_failure("dns_health_check", code)
            # Docker Running alone misses SIGSTOP/deadlocks. Read the trusted
            # heartbeat INSIDE the helper; no host path or payload is exposed.
            # The marker is written only AFTER successful kernel lease refresh.
            result = self.domain_gateway.exec_run([
                "python3", "-c",
                ("import json,time,sys;v=json.load(open('/run/aw-dns-health'));"
                 "sys.exit(0 if v['execution_id']==sys.argv[1] and 0<=time.monotonic()-v['at']<0.8 else 1)"),
                self.execution_id,
            ])
            if result.exit_code != 0:
                self._raise_gateway_failure("dns_health_check", "health_expired")
        except (APIError, DockerException, OSError) as exc:
            raise self._dns_error("dns_health_check", "unknown") from exc

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
        sources = [self.source_ip]
        if self.source_ipv6:
            sources.append(self.source_ipv6)
        self.started_at = int(datetime.now(UTC).timestamp())
        try:
            self.watcher = self.client.containers.run(
                NET_IMAGE,
                command=["python3", "-u", "/opt/aw/packet_capture.py", self.bridge, *sources],
                network_mode="host", cap_add=["NET_RAW"], detach=True, remove=False,
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

    def _domain_events(self) -> list[dict[str, Any]]:
        if self.domain_gateway is None:
            return []
        raw = self.domain_gateway.logs()
        text = raw.decode("utf-8", "strict") if isinstance(raw, bytes) else str(raw)
        ready = False
        events: list[dict[str, Any]] = []
        try:
            for line in text.splitlines():
                if not line.startswith("{"):
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise self._dns_error("dns_audit", "diagnostic_invalid")
                self._check_dns_failure(event)
                if event.get("aw_dns_ready") == self.execution_id:
                    ready = True
                elif event.get("aw_dns") == 1:
                    if event.get("execution_id") != self.execution_id or event.get("seq") != len(events) + 1:
                        raise self._dns_error("dns_audit", "diagnostic_invalid")
                    events.append(event)
            if not ready:
                raise self._dns_error("dns_audit", "diagnostic_invalid")
        except (ValueError, UnicodeError) as exc:
            raise self._dns_error("dns_audit", "diagnostic_invalid") from exc
        return events

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
                raise self._dns_error("packet_audit", "capture_exited")
            if not re.search(r"\d+ packets? dropped by kernel", diagnostics) or any(int(n) for n in drops):
                raise self._dns_error("packet_audit", "capture_incomplete")
            events = self.watcher.logs(stdout=True, stderr=False)
            packet_text = events.decode("utf-8", "replace") if isinstance(events, bytes) else events
            captured = re.search(r"(\d+) packets? captured", diagnostics)
            if captured is None or int(captured.group(1)) != len(_IP_HEADER_RE.findall(packet_text)):
                raise self._dns_error("packet_audit", "capture_truncated")
            records = parse_packet_output(packet_text, source_ip=self.source_ip)
            if self.source_ipv6:
                records.extend(parse_packet_output(events, source_ip=self.source_ipv6))
            records.sort(key=lambda record: record.get("occurred_at", ""))
            self.assert_healthy()
            domain_events = self._domain_events()
            if self.domain_config is None:
                dns_logs = self.dns_proxy.logs(since=self.started_at)
                ip_domains = _dns_ip_domains(dns_logs, self.source_ip)
                for record in records:
                    record["domain"] = ip_domains.get(record["target_ip"], "ip-direct")
            else:
                # Only this execution's trusted, pre-response bindings can give
                # domain authority. Shared dnsmasq logs are not consulted here.
                for record in records:
                    when = datetime.fromisoformat(record["occurred_at"]).timestamp()
                    matching = [e for e in domain_events if e["action"] == "allow"
                                and record["target_ip"] in e.get("addresses", [])
                                and e["time"] <= when < e["time"] + e.get("ttl", 0)]
                    if matching:
                        record["domain"] = matching[-1]["domain"]
            for record in records:
                if record.pop("_kernel_denied", False):
                    record.update(action="deny", matched_rule_id=None)
                    continue
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
                if self.domain_config is not None:
                    when = datetime.fromisoformat(record["occurred_at"]).timestamp()
                    blocked = [r for e in domain_events
                               if record["target_ip"] in e.get("addresses", [])
                               and e["time"] <= when < e["time"] + e.get("ttl", 0)
                               for r in e.get("denied_rules", [])
                               if not r.get("port_range") or (
                                   int(r["port_range"].split("-")[0]) <= record["port"] <=
                                   int(r["port_range"].split("-")[-1]))]
                    if blocked:
                        record.update(action="deny", matched_rule_id=blocked[-1].get("id"))
                    elif (self.mode == "default" and record["domain"] == "ip-direct"
                          and any(r.action == "deny" and r.target_type == "domain" for r in self.global_rules)):
                        record.update(action="deny", matched_rule_id=None)
                record.setdefault("occurred_at", datetime.now(UTC).isoformat())
            for event in domain_events:
                if event["action"] == "deny":
                    # A DNS policy refusal is attributable even if no subsequent
                    # TCP SYN exists (CNAME blacklist/unknown-name denial).
                    records.append({
                        "source_ip": self.source_ip, "source_port": event.get("source_port", 0),
                        "target_ip": self.dns_proxy_ip, "port": 53, "protocol": event.get("protocol", "udp"),
                        "domain": event["domain"], "action": "deny",
                        "matched_rule_id": event.get("matched_rule_id"), "bytes_out": None,
                        "occurred_at": datetime.fromtimestamp(event["time"], UTC).isoformat(),
                    })
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
        if self.domain_gateway is not None:
            try:
                self.domain_gateway.remove(force=True)
            except (APIError, DockerException, OSError, RuntimeError) as cleanup_exc:
                logger.debug("domain gateway cleanup failed: %s", cleanup_exc)
            self.domain_gateway = None
        try:
            self._run_commands(self.cleanup_commands, best_effort=True)
        except NetworkPolicyError:
            # Only execution-owned chains/table; never flush global host rules.
            pass
        try:
            self.network.disconnect(self.dns_proxy, force=True)
        except (APIError, DockerException, OSError, RuntimeError) as cleanup_exc:
            logger.debug("DNS proxy detach failed: %s", cleanup_exc)
