"""Trusted execution-private DNS policy; no app imports, network IO, or shell.

DNS authorizes destinations, not TLS identities: once an address is authorized,
all names served by that address share the authorization on the permitted ports.
Global domain denies put an execution into DNS-bound default mode: unseen direct
IPs are not allowed unless an explicit IP/CIDR allow exists in restricted mode.
A denied alias/address remains denied for its observed TTL, even if another name
subsequently returns it. Unknown aliases cannot be inferred from an IP address.
"""
from __future__ import annotations

import ipaddress
import re
import struct
from dataclasses import dataclass

MAX_WIRE = 4096
MAX_RECORDS = 64
MAX_CHAIN = 8
MAX_TTL = 300
MAX_BINDINGS = 1024
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
# Unconditionally deny non-public, transition, mapped, multicast and metadata
# destinations even when an old deployment's builtin configuration is incomplete.
UNSAFE = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4",
    "240.0.0.0/4", "::/96", "::ffff:0:0/96", "64:ff9b::/96",
    "64:ff9b:1::/48", "100::/64", "2001::/32", "2002::/16",
    "fc00::/7", "fe80::/10", "ff00::/8",
)]


class PolicyError(ValueError):
    pass


def name(value: str, *, wildcard: bool = False) -> str:
    if not isinstance(value, str):
        raise PolicyError("invalid DNS name")
    result = value.rstrip(".").lower()
    base = result[2:] if wildcard and result.startswith("*.") else result
    if len(result) > 253 or "." not in base or not all(_LABEL.fullmatch(x) for x in base.split(".")):
        raise PolicyError("invalid DNS name")
    return result


def matches(pattern: str, value: str) -> bool:
    base = pattern.removeprefix("*.")
    return value == base or (pattern.startswith("*.") and value.endswith("." + base))


def read_name(wire: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    seen: set[int] = set()
    end = None
    while True:
        if offset >= len(wire) or offset in seen or len(seen) >= 128:
            raise PolicyError("invalid DNS compression")
        seen.add(offset)
        size = wire[offset]
        if size & 0xC0 == 0xC0:
            if offset + 1 >= len(wire):
                raise PolicyError("short DNS pointer")
            pointer = ((size & 0x3F) << 8) | wire[offset + 1]
            # Forward/self pointers are not needed by a well-formed answer.
            if pointer >= offset:
                raise PolicyError("forward DNS compression pointer")
            if end is None:
                end = offset + 2
            offset = pointer
            continue
        if size & 0xC0 or size > 63:
            raise PolicyError("invalid DNS label")
        offset += 1
        if size == 0:
            break
        if offset + size > len(wire):
            raise PolicyError("short DNS label")
        try:
            labels.append(wire[offset:offset + size].decode("ascii"))
        except UnicodeDecodeError as exc:
            raise PolicyError("non ASCII DNS label") from exc
        offset += size
    return name(".".join(labels)), end if end is not None else offset


def encode_name(value: str) -> bytes:
    return b"".join(bytes([len(x)]) + x.encode("ascii") for x in name(value).split(".")) + b"\0"


@dataclass(frozen=True)
class Question:
    identity: int
    domain: str
    kind: int

    def wire(self) -> bytes:
        return struct.pack("!6H", self.identity, 0x100, 1, 0, 0, 0) + self.body()

    def body(self) -> bytes:
        return encode_name(self.domain) + struct.pack("!HH", self.kind, 1)


def question(wire: bytes) -> Question:
    if not 12 <= len(wire) <= MAX_WIRE:
        raise PolicyError("invalid DNS size")
    identity, flags, qd, an, ns, ar = struct.unpack_from("!6H", wire)
    if flags & 0xF800 or qd != 1 or an or ns or ar > 1:
        raise PolicyError("unsupported DNS question")
    domain, end = read_name(wire, 12)
    if end + 4 > len(wire):
        raise PolicyError("short DNS question")
    kind, cls = struct.unpack_from("!HH", wire, end)
    if cls != 1 or kind not in (1, 28):
        # No HTTPS/SVCB/address hints, ANY, AXFR, or alternate DNS classes.
        raise PolicyError("only IN A/AAAA queries are supported")
    return Question(identity, domain, kind)


@dataclass(frozen=True)
class Record:
    owner: str
    kind: int
    ttl: int
    value: str

    def wire(self, ttl: int) -> bytes:
        data = encode_name(self.value) if self.kind == 5 else ipaddress.ip_address(self.value).packed
        return encode_name(self.owner) + struct.pack("!HHIH", self.kind, 1, ttl, len(data)) + data


def answer(wire: bytes, expected: Question) -> tuple[list[Record], int]:
    if not 12 <= len(wire) <= MAX_WIRE:
        raise PolicyError("invalid DNS answer size")
    identity, flags, qd, an, ns, ar = struct.unpack_from("!6H", wire)
    if identity != expected.identity or not flags & 0x8000 or flags & 0x7A00 or qd != 1:
        raise PolicyError("mismatched or truncated DNS answer")
    owner, end = read_name(wire, 12)
    if end + 4 > len(wire) or (owner, *struct.unpack_from("!HH", wire, end)) != (expected.domain, expected.kind, 1):
        raise PolicyError("mismatched DNS question")
    if an + ns + ar > MAX_RECORDS:
        raise PolicyError("too many DNS records")
    end += 4
    records: list[Record] = []
    for index in range(an + ns + ar):
        owner, end = read_name(wire, end)
        if end + 10 > len(wire):
            raise PolicyError("short DNS record")
        kind, cls, ttl, size = struct.unpack_from("!HHIH", wire, end)
        end += 10
        stop = end + size
        if stop > len(wire):
            raise PolicyError("short DNS data")
        if index < an:
            if cls != 1 or kind not in (1, 28, 5):
                raise PolicyError("unsupported answer record")
            if kind == 5:
                value, consumed = read_name(wire, end)
                if consumed != stop:
                    raise PolicyError("invalid CNAME data")
            else:
                if size != (4 if kind == 1 else 16):
                    raise PolicyError("invalid address record")
                value = str(ipaddress.ip_address(wire[end:stop]))
            records.append(Record(owner, kind, min(ttl, MAX_TTL), value))
        end = stop
    if end != len(wire):
        raise PolicyError("trailing DNS data")
    return records, flags & 15


def chain(records: list[Record], initial: str, kind: int) -> tuple[list[Record], str]:
    current = initial
    used: list[Record] = []
    visited: set[str] = set()
    for _ in range(MAX_CHAIN + 1):
        if current in visited:
            raise PolicyError("CNAME loop")
        visited.add(current)
        cnames = [r for r in records if r.owner == current and r.kind == 5]
        addresses = [r for r in records if r.owner == current and r.kind in (1, 28)]
        if cnames:
            if len(cnames) != 1 or addresses:
                raise PolicyError("ambiguous CNAME")
            used.extend(cnames)
            current = cnames[0].value
        else:
            if any(r.kind != kind for r in addresses):
                raise PolicyError("unexpected address family")
            used.extend(addresses)
            if len(used) != len(records):
                raise PolicyError("unrelated DNS answer records")
            return used, current
    raise PolicyError("CNAME chain too long")


def response(q: Question, records: list[Record], *, rcode: int = 0, ttl: int | None = None) -> bytes:
    result = struct.pack("!6H", q.identity, 0x8180 | rcode, 1, len(records), 0, 0) + q.body()
    result += b"".join(r.wire(r.ttl if ttl is None else min(ttl, r.ttl)) for r in records)
    if len(result) > MAX_WIRE:
        raise PolicyError("sanitized DNS response too large")
    return result


class Policy:
    def __init__(self, config: dict):
        self.mode = config["mode"]
        if self.mode not in ("default", "restricted"):
            raise PolicyError("invalid mode")
        self.global_rules = self._rules(config.get("global_rules", []), "deny")
        self.agent_rules = self._rules(config.get("agent_rules", []), "allow")
        self.builtins = [*UNSAFE, *(ipaddress.ip_network(x, strict=False) for x in config["builtin_blacklist"])]

    @staticmethod
    def _rules(values: list, action: str) -> list[dict]:
        if len(values) > 128:
            raise PolicyError("too many egress rules")
        rules = []
        for value in values:
            if value["action"] != action:
                continue
            rule = dict(value)
            if rule["target_type"] == "domain":
                rule["target"] = name(rule["target"], wildcard=True)
            elif rule["target_type"] in ("ip", "cidr"):
                rule["target"] = str(ipaddress.ip_network(rule["target"], strict=False))
            else:
                raise PolicyError("invalid rule type")
            raw = rule.get("port_range")
            if raw:
                if not re.fullmatch(r"\d{1,5}(?:-\d{1,5})?", raw):
                    raise PolicyError("invalid rule ports")
                parts = [int(x) for x in raw.split("-")]
                if not 1 <= min(parts) <= max(parts) <= 65535:
                    raise PolicyError("invalid rule ports")
                rule["port_range"] = str(min(parts)) if len(parts) == 1 else f"{min(parts)}-{max(parts)}"
            rules.append(rule)
        return rules

    def denies(self, names: list[str]) -> list[int]:
        return [i for i, r in enumerate(self.global_rules)
                if r["target_type"] == "domain" and any(matches(r["target"], n) for n in names)]

    def allows(self, original: str) -> list[int]:
        return [i for i, r in enumerate(self.agent_rules)
                if r["target_type"] == "domain" and matches(r["target"], original)]

    def safe(self, value: str) -> bool:
        ip = ipaddress.ip_address(value)
        return not any(ip.version == n.version and ip in n for n in self.builtins)

    def permitted_query(self, original: str) -> bool:
        # Denied names are resolved privately for blacklist binding, never released.
        return self.mode == "default" or bool(self.allows(original))


def table_name(execution_id: str) -> str:
    import uuid
    return "awd_" + uuid.UUID(execution_id).hex


def nft_policy(config: dict, policy: Policy) -> str:
    """One atomic nft transaction, scoped exclusively to this execution bridge.

    No conntrack ACCEPT shortcut: TTL, blacklist and health are checked on EVERY
    packet, including packets on existing TCP/UDP connections. DNS and IPv6 ND
    control traffic are the only exceptions. A lease starts EMPTY (fail closed).
    """
    table = table_name(config["execution_id"])
    bridge = config["bridge"]
    if not re.fullmatch(r"br-[0-9a-fA-F]{6,64}", bridge):
        raise PolicyError("invalid execution bridge")
    source4 = str(ipaddress.IPv4Address(config["source_ip"]))
    source6 = str(ipaddress.IPv6Address(config["source_ipv6"])) if config.get("source_ipv6") else None
    sets = [f"set alive{family} {{ type ipv{family}_addr; flags timeout; timeout 1s; size 1; }}" for family in (4, 6)]
    sets.extend(f"set {prefix}{family} {{ type ipv{family}_addr; flags timeout; timeout 300s; size {MAX_BINDINGS}; }}"
                for prefix in ["seen", *[f"a{i}_" for i in range(len(policy.agent_rules))],
                               *[f"d{i}_" for i in range(len(policy.global_rules))]] for family in (4, 6))
    chains: list[str] = []
    for direction in ("out", "back"):
        address_key = "daddr" if direction == "out" else "saddr"
        port_key = "dport" if direction == "out" else "sport"
        health_key = "saddr" if direction == "out" else "daddr"
        rows = [f"meta nfproto ipv4 ip {health_key} != @alive4 drop",
                f"meta nfproto ipv6 ip6 {health_key} != @alive6 drop"]
        if direction == "out":
            rows.append(f"meta nfproto ipv4 ip saddr != {source4} drop")
            rows.append(f"meta nfproto ipv6 ip6 saddr != {source6} drop" if source6 else "meta nfproto ipv6 drop")
        else:
            rows.append("ct state != established drop")
        # Only fully observable TCP/UDP. Fragments/IPv6 extension chains are
        # rejected rather than opening protocol-specific audit gaps.
        rows.extend(["ip frag-off & 0x3fff != 0 drop", "meta nfproto ipv6 ip6 nexthdr != { tcp, udp } drop",
                     "meta l4proto != { tcp, udp } drop"])
        if direction == "out":
            rows.append("th dport 53 drop")
        for net in dict.fromkeys(policy.builtins):
            family = "ip" if net.version == 4 else "ip6"
            rows.append(f"{family} {address_key} {net} drop")
        for prefix, rules, verdict in (("d", policy.global_rules, "drop"), ("a", policy.agent_rules, "accept")):
            if prefix == "a" and policy.mode != "restricted":
                continue
            for index, rule in enumerate(rules):
                port = f" th {port_key} {rule['port_range']}" if rule.get("port_range") else ""
                if rule["target_type"] == "domain":
                    for family in (4, 6):
                        ip = "ip" if family == 4 else "ip6"
                        rows.append(f"{ip} {address_key} @{prefix}{index}_{family}{port} {verdict}")
                else:
                    net = ipaddress.ip_network(rule["target"])
                    ip = "ip" if net.version == 4 else "ip6"
                    rows.append(f"{ip} {address_key} {net}{port} {verdict}")
        if policy.mode == "default":
            # A global domain blacklist cannot authorize unknown direct IPs.
            # Requiring an observed safe DNS binding makes this fail closed.
            if any(r["target_type"] == "domain" for r in policy.global_rules):
                rows += [f"ip {address_key} @seen4 accept", f"ip6 {address_key} @seen6 accept"]
            else:
                rows.append("accept")
        rows.append("drop")
        chains.append(f"chain {direction} {{ " + "; ".join(rows) + "; }")
    chains.append(f'chain forward {{ type filter hook forward priority -10; policy accept; iifname "{bridge}" jump out; oifname "{bridge}" jump back; }}')
    return f"table inet {table} {{\n" + "\n".join([*sets, *chains]) + "\n}\n"
