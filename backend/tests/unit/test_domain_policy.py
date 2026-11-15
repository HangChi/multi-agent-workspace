"""Pure T060 tests; intentionally no backend/conftest.py database fixtures."""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPERS = ROOT / "deploy" / "sbx-net"
# The Docker build context owns this exact source; tests exercise it, not a copy.
for module_name in ("domain_policy", "dns_mediator"):
    spec = importlib.util.spec_from_file_location(module_name, HELPERS / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

from dns_mediator import Binder, Mediator
from domain_policy import (
    MAX_BINDINGS,
    Policy,
    PolicyError,
    Question,
    Record,
    answer,
    chain,
    nft_policy,
    question,
    response,
)

EID = "f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a"


def rule(target, action="allow", ports=None):
    return {"id": target, "target": target, "target_type": "domain", "action": action, "port_range": ports}


def config(**overrides):
    result = {"execution_id": EID, "mode": "restricted", "bridge": "br-abc123",
              "source_ip": "172.30.0.3", "source_ipv6": None, "listen_ip": "172.30.0.1",
              "upstream_ip": "172.30.0.2", "builtin_blacklist": [], "global_rules": [],
              "agent_rules": [rule("*.example.com", ports="443")]}
    return {**result, **overrides}


def mediator(cfg=None, replies=None, execute=None):
    cfg = cfg or config()
    calls = []
    binder = Binder(cfg, Policy(cfg), execute=execute or calls.append, clock=lambda: 0)
    replies = replies or {"www.example.com": [Record("www.example.com", 1, 30, "93.184.216.34")]}
    resolver = lambda q, deadline: response(q, replies[q.domain])
    return Mediator(cfg, resolver=resolver, binder=binder, clock=lambda: 0), calls


def test_runtime_domain_snapshot_starts_execution_private_mediator():
    from app.executors.egress_network import NetworkCapture
    proxy = Mock()
    proxy.attrs = {"NetworkSettings": {"Networks": {"sandbox": {"IPAddress": "172.30.0.2"}}}}
    network = SimpleNamespace(name="sandbox", connect=Mock(), disconnect=Mock(), attrs={
        "Id": "abcdef123456", "IPAM": {"Config": [{"Gateway": "172.30.0.1"}]}})
    helper = Mock()
    helper.logs.return_value = json.dumps({"aw_dns_ready": EID}).encode()
    helper.attrs = {"State": {"Running": True}}
    helper.exec_run.return_value = SimpleNamespace(exit_code=0)
    client = Mock()
    client.containers.get.return_value = proxy
    client.containers.run.side_effect = lambda *args, **kwargs: helper if kwargs.get("detach") else b""
    capture = NetworkCapture.prepare(client, network, EID, config())
    assert capture.dns_proxy_ip == "172.30.0.1"
    assert capture.domain_gateway is helper
    launches = [c.kwargs for c in client.containers.run.call_args_list]
    gateway = next(c for c in launches if "/opt/aw/dns_mediator.py" in c["command"])
    assert gateway["network_mode"] == "host"
    assert gateway["cap_add"] == ["NET_ADMIN", "NET_BIND_SERVICE"]
    assert gateway["read_only"] and gateway["pids_limit"] <= 64


@pytest.mark.parametrize("domain,allowed", [
    ("example.com", True), ("a.example.com", True), ("a.b.example.com", True),
    ("example.com.evil.net", False), ("notexample.com", False),
])
def test_exact_and_wildcard_are_label_bounded(domain, allowed):
    policy = Policy(config())
    assert policy.permitted_query(domain) is allowed
    exact = Policy(config(agent_rules=[rule("example.com")]))
    assert exact.permitted_query(domain) is (domain == "example.com")


@pytest.mark.parametrize("target", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "100.64.0.1",
                                     "::1", "::ffff:127.0.0.1", "64:ff9b::a00:1", "fe80::1", "fc00::1", "ff02::1"])
def test_dns_rebinding_private_mapped_transition_multicast_targets_refused(target):
    kind = 28 if ":" in target else 1
    med, calls = mediator(replies={"www.example.com": [Record("www.example.com", kind, 30, target)]})
    result = med.process(Question(1, "www.example.com", kind).wire(), "172.30.0.3")
    assert result is not None and struct.unpack_from("!H", result, 2)[0] & 15 == 5
    assert not calls


def test_binding_precedes_dns_response_and_preserves_ports():
    med, calls = mediator()
    result = med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3")
    assert result and calls and "a0_4 { 93.184.216.34 timeout 29500ms }" in calls[0]
    script = nft_policy(config(), med.policy)
    assert "ip daddr @a0_4 th dport 443 accept" in script
    assert "ip6 daddr @a0_6 th dport 443 accept" in script
    assert "ct state established accept" not in script


def test_ipv6_binding_is_independent_of_ipv4():
    med, calls = mediator(replies={"www.example.com": [Record("www.example.com", 28, 20, "2606:4700:4700::1111")]})
    result = med.process(Question(1, "www.example.com", 28).wire(), "172.30.0.3")
    assert result and "a0_6 { 2606:4700:4700::1111 timeout 19500ms }" in calls[0]
    assert "a0_4" not in calls[0]


def test_cross_execution_source_cannot_create_bindings():
    med, calls = mediator()
    assert med.process(Question(1, "www.example.com", 1).wire(), "172.31.0.3") is None
    assert not calls
    assert med.requests == 0


def test_cname_chain_followed_and_ttl_is_minimum_of_entire_chain():
    med, calls = mediator(replies={
        "www.example.com": [Record("www.example.com", 5, 7, "cdn.vendor.net")],
        "cdn.vendor.net": [Record("cdn.vendor.net", 1, 90, "93.184.216.34")],
    })
    q = Question(1, "www.example.com", 1)
    wire = med.process(q.wire(), "172.30.0.3")
    records, _ = answer(wire, q)
    assert [r.ttl for r in records] == [6, 6]
    assert "timeout 6500ms" in calls[0]


def test_cname_global_blacklist_beats_original_domain_allow_and_is_kernel_bound(capsys):
    med, calls = mediator(config(global_rules=[rule("*.vendor.net", "deny")]), replies={
        "www.example.com": [Record("www.example.com", 5, 30, "cdn.vendor.net"),
                            Record("cdn.vendor.net", 1, 20, "93.184.216.34")],
    })
    wire = med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3")
    assert struct.unpack_from("!H", wire, 2)[0] & 15 == 5
    assert "d0_4 { 93.184.216.34 timeout 20000ms }" in calls[0]
    assert "a0_4" not in calls[0]
    event = json.loads(capsys.readouterr().out)
    assert event["action"] == "deny" and event["matched_rule_id"] == "*.vendor.net"
    script = nft_policy(config(global_rules=[rule("*.vendor.net", "deny")]), med.policy)
    assert script.index("@d0_4") < script.index("@a0_4")


@pytest.mark.parametrize("records", [
    [Record("www.example.com", 5, 30, "www.example.com")],
    [Record("www.example.com", 5, 30, "cdn.example.com"), Record("www.example.com", 1, 30, "93.184.216.34")],
    [Record("www.example.com", 1, 30, "93.184.216.34"), Record("evil.net", 1, 30, "1.1.1.1")],
    [Record("www.example.com", 28, 30, "2606:4700::1111")],
])
def test_malicious_cname_loops_ambiguity_unrelated_and_family_confusion_are_fatal(records):
    med, calls = mediator(replies={"www.example.com": records})
    assert med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3") is None
    assert med.dead.is_set() and not calls


def test_binding_failure_returns_no_dns_answer_and_closes_health_lease():
    fail = Mock(side_effect=PolicyError("kernel failure"))
    med, _ = mediator(execute=fail)
    assert med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3") is None
    assert med.dead.is_set()


def test_zero_ttl_is_refused_never_turned_into_permanent_lease():
    med, calls = mediator(replies={"www.example.com": [Record("www.example.com", 1, 0, "93.184.216.34")]})
    assert med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3") is None
    assert not calls


def test_kernel_policy_checks_health_ttl_existing_connections_and_unseen_direct_ip():
    cfg = config(mode="default", global_rules=[rule("*.blocked.net", "deny")])
    script = nft_policy(cfg, Policy(cfg))
    assert "timeout 1s; size 1" in script
    assert "ip saddr != @alive4 drop" in script
    assert "ip6 saddr != @alive6 drop" in script
    assert "ip daddr @seen4 accept" in script
    assert "ip6 daddr @seen6 accept" in script
    assert "frag-off" in script and "ip6 nexthdr != { tcp, udp } drop" in script
    assert "ct state established accept" not in script


def test_execution_tables_are_disjoint():
    left = nft_policy(config(), Policy(config()))
    cfg = config(execution_id="d4e784e5-1dd9-4ae1-84ad-a0c66bdb761a")
    right = nft_policy(cfg, Policy(cfg))
    assert EID.replace("-", "") in left and EID.replace("-", "") not in right


def test_binding_budget_refuses_instead_of_eviction():
    cfg = config()
    binder = Binder(cfg, Policy(cfg), execute=Mock(), clock=lambda: 0)
    binder.bindings = {("seen4", str(ipaddress.IPv4Address(0x5D000000 + n))): 100 for n in range(MAX_BINDINGS)}
    with pytest.raises(PolicyError, match="budget"):
        binder.bind(["1.1.1.1"], ["www.example.com"], "www.example.com", 30)
    binder.execute.assert_not_called()


def test_expired_bindings_reclaimed_without_shortening_global_denies():
    cfg = config(global_rules=[rule("*.blocked.net", "deny")])
    clock = [0]
    binder = Binder(cfg, Policy(cfg), execute=Mock(), clock=lambda: clock[0])
    binder.bind(["93.184.216.34"], ["a.blocked.net"], "a.blocked.net", 30)
    clock[0] = 5
    binder.bind(["93.184.216.34"], ["a.blocked.net"], "a.blocked.net", 3)
    assert "timeout 25000ms" in binder.execute.call_args.args[0]
    clock[0] = 31
    binder.bind(["1.1.1.1"], ["www.example.com"], "www.example.com", 10)
    assert ("d0_4", "93.184.216.34") not in binder.bindings


@pytest.mark.parametrize("wire", [b"", b"\0" * 12, b"\0\1\1\0\0\1\0\0\0\0\0\0\xc0\x0c\0\1\0\1"])
def test_malformed_dns_and_compression_loops_rejected(wire):
    with pytest.raises(PolicyError):
        question(wire)


def test_truncated_spoofed_response_and_unexpected_transaction_rejected():
    q = Question(1, "www.example.com", 1)
    valid = response(q, [Record(q.domain, 1, 30, "93.184.216.34")])
    for bad in [valid[:-1], response(Question(2, q.domain, 1), []), valid[:2] + b"\x83\x80" + valid[4:]]:
        with pytest.raises(PolicyError):
            answer(bad, q)


def test_unknown_query_types_do_not_release_address_hints():
    q = Question(1, "www.example.com", 65)
    with pytest.raises(PolicyError):
        question(q.wire())


def test_cname_depth_is_bounded():
    records = [Record(f"a{i}.example.com", 5, 30, f"a{i + 1}.example.com") for i in range(10)]
    with pytest.raises(PolicyError, match="long"):
        chain(records, "a0.example.com", 1)


def test_partial_port_domain_blacklist_binds_deny_before_allow_but_keeps_other_ports():
    cfg = config(global_rules=[rule("*.vendor.net", "deny", "443")])
    med, calls = mediator(cfg, replies={"www.example.com": [
        Record("www.example.com", 5, 30, "cdn.vendor.net"),
        Record("cdn.vendor.net", 1, 20, "93.184.216.34"),
    ]})
    wire = med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3")
    assert wire and struct.unpack_from("!H", wire, 2)[0] & 15 == 0
    assert "d0_4" in calls[0] and "a0_4" in calls[0]
    script = nft_policy(cfg, med.policy)
    assert script.index("@d0_4 th dport 443 drop") < script.index("@a0_4")


def test_cname_resolution_elapsed_time_cannot_extend_alias_ttl():
    cfg = config()
    clock = [0]
    calls = []
    binder = Binder(cfg, Policy(cfg), execute=calls.append)

    def resolve(q, deadline):
        if q.domain == "www.example.com":
            return response(q, [Record(q.domain, 5, 1, "cdn.vendor.net")])
        clock[0] += 1.2
        return response(q, [Record(q.domain, 1, 60, "93.184.216.34")])

    med = Mediator(cfg, resolver=resolve, binder=binder, clock=lambda: clock[0])
    assert med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3") is None
    assert not calls and med.dead.is_set()


def test_mixed_private_public_blacklist_answer_still_revokes_public_address():
    cfg = config(global_rules=[rule("*.example.com", "deny")])
    med, calls = mediator(cfg, replies={"www.example.com": [
        Record("www.example.com", 1, 30, "93.184.216.34"),
        Record("www.example.com", 1, 30, "127.0.0.1"),
    ]})
    wire = med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3")
    assert struct.unpack_from("!H", wire, 2)[0] & 15 == 5
    assert "d0_4" in calls[0] and "93.184.216.34" in calls[0]
    assert "127.0.0.1" not in calls[0] and "a0_4" not in calls[0]


@pytest.mark.parametrize("state,exit_code", [({"Running": False}, 0), ({"Running": True, "Paused": True}, 0), ({"Running": True}, 1)])
def test_dead_paused_and_stale_heartbeat_all_fail_closed(state, exit_code):
    from app.executors.egress_network import NetworkCapture, NetworkPolicyError
    gateway = Mock(attrs={"State": state})
    gateway.exec_run.return_value = SimpleNamespace(exit_code=exit_code)
    cap = NetworkCapture(client=Mock(), network=Mock(), execution_id=EID, mode="default",
                         global_rules=[], agent_rules=[], builtin_blacklist=[], dns_proxy=Mock(),
                         dns_proxy_ip="172.30.0.1", bridge="br-abc123", install_commands=[],
                         cleanup_commands=[], domain_gateway=gateway)
    with pytest.raises(NetworkPolicyError):
        cap.assert_healthy()


def test_dns_denials_keep_actual_tcp_source_port(capsys):
    med, _ = mediator()
    q = Question(1, "not-allowed.test", 1)
    med.process(q.wire(), "172.30.0.3", protocol="tcp", source_port=41001)
    event = json.loads(capsys.readouterr().out)
    assert event["protocol"] == "tcp" and event["source_port"] == 41001


def test_upstream_failure_returns_no_usable_answer_and_closes_gateway():
    med, calls = mediator()
    med.resolver = Mock(side_effect=TimeoutError("upstream did not respond"))
    assert med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3") is None
    assert med.dead.is_set() and not calls


def test_request_budget_is_execution_local_and_fatal():
    import dns_mediator
    med, calls = mediator()
    med.requests = dns_mediator.MAX_REQUESTS
    assert med.process(Question(1, "www.example.com", 1).wire(), "172.30.0.3") is None
    assert med.dead.is_set() and not calls


def test_heartbeat_refresh_and_binding_changes_are_single_atomic_transactions():
    cfg = config(source_ipv6="2001:db8::3")
    calls = []
    binder = Binder(cfg, Policy(cfg), execute=calls.append)
    binder.heartbeat()
    assert len(calls) == 1 and "flush set" in calls[0]
    assert "alive4" in calls[0] and "alive6" in calls[0]
    binder.bind(["93.184.216.34", "2606:4700::1111"], ["www.example.com"], "www.example.com", 30)
    assert len(calls) == 2 and "a0_4" in calls[1] and "a0_6" in calls[1]


def test_expired_lease_cannot_be_restarted_after_waiting_for_kernel_lock():
    cfg = config()
    calls = []
    clock = [0]
    binder = Binder(cfg, Policy(cfg), execute=calls.append, clock=lambda: clock[0])
    clock[0] = 5
    with pytest.raises(PolicyError, match="expired"):
        binder.bind(["93.184.216.34"], ["www.example.com"], "www.example.com", 30, valid_until=4)
    assert not calls


def test_allow_kernel_timeout_reserves_bounded_transaction_latency():
    cfg = config()
    calls = []
    binder = Binder(cfg, Policy(cfg), execute=calls.append, clock=lambda: 5)
    binder.bind(["93.184.216.34"], ["www.example.com"], "www.example.com", 30, valid_until=7)
    assert "timeout 1500ms" in calls[0]
