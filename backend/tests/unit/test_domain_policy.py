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
for module_name in ("domain_policy", "dns_mediator", "command_batch"):
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


def test_runtime_domain_snapshot_refreshes_lazy_docker_ipam():
    """Docker may populate automatic gateways only after the first attachment."""
    from app.executors.egress_network import NetworkCapture

    proxy = Mock()
    proxy.attrs = {"NetworkSettings": {"Networks": {"sandbox": {"IPAddress": "172.30.0.2"}}}}
    network = Mock(name="sandbox-network")
    network.name = "sandbox"
    network.attrs = {"Id": "abcdef123456", "IPAM": {"Config": [{"Subnet": "172.30.0.0/24"}]}}

    def reload_network():
        network.attrs["IPAM"]["Config"][0]["Gateway"] = "172.30.0.1"

    network.reload.side_effect = reload_network
    helper = Mock()
    helper.logs.return_value = json.dumps({"aw_dns_ready": EID}).encode()
    helper.attrs = {"State": {"Running": True}}
    helper.exec_run.return_value = SimpleNamespace(exit_code=0)
    client = Mock()
    client.containers.get.return_value = proxy
    client.containers.run.side_effect = lambda *args, **kwargs: helper if kwargs.get("detach") else b""
    capture = NetworkCapture.prepare(client, network, EID, config())
    network.reload.assert_called_once()
    assert capture.dns_proxy_ip == "172.30.0.1"
    assert capture.domain_config["upstream_ip"] == "172.30.0.2"


def test_runtime_domain_snapshot_starts_execution_private_mediator():
    from app.executors.egress_network import NetworkCapture
    proxy = Mock()
    proxy.attrs = {"NetworkSettings": {"Networks": {"sandbox": {"IPAddress": "172.30.0.2"}}}}
    network = SimpleNamespace(name="sandbox", connect=Mock(), disconnect=Mock(), reload=Mock(), attrs={
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


@pytest.mark.parametrize("stderr,code", [
    (b"Operation not permitted secret-address", "permission_denied"),
    (b"syntax error, unexpected secret-rule", "nft_syntax"),
    (b"Operation not supported secret-feature", "nft_feature_unavailable"),
    (b"Could not process rule: No such file or directory secret-set", "nft_feature_unavailable"),
    (b"unrecognized secret-message", "nft_transaction_failed"),
])
def test_nft_failure_retains_finite_cause_not_stderr(monkeypatch, stderr, code):
    import dns_mediator
    monkeypatch.setattr(dns_mediator.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=1, stderr=stderr)))
    with pytest.raises(PolicyError) as failure:
        dns_mediator.nft("trusted-policy")
    assert getattr(failure.value, "code", None) == code
    assert "secret" not in str(failure.value)


def test_startup_worker_preserves_matching_trusted_failure_before_destroying_helper():
    from app.executors.egress_network import NetworkCapture, NetworkPolicyError
    helper = Mock(attrs={"State": {"Running": False}})
    helper.logs.return_value = json.dumps({"aw_dns_failure": 1, "execution_id": EID,
                                          "stage": "dns_nft_install", "code": "nft_syntax"}).encode()
    capture = NetworkCapture(client=Mock(), network=Mock(), execution_id=EID, mode="default",
                             global_rules=[], agent_rules=[], builtin_blacklist=[], dns_proxy=Mock(),
                             dns_proxy_ip="172.30.0.1", bridge="br-abc123", install_commands=[],
                             cleanup_commands=[], domain_config=config())
    capture.client.containers.run.return_value = helper
    with pytest.raises(NetworkPolicyError, match=r"dns_failure\[dns_nft_install,nft_syntax\]"):
        capture._start_domain_gateway()
    helper.logs.assert_called_with(tail=32, stdout=True, stderr=False)


def diagnostic_capture(helper):
    from app.executors.egress_network import NetworkCapture
    return NetworkCapture(client=Mock(), network=Mock(), execution_id=EID, mode="default",
                          global_rules=[], agent_rules=[], builtin_blacklist=[], dns_proxy=Mock(),
                          dns_proxy_ip="172.30.0.1", bridge="br-abc123", install_commands=[],
                          cleanup_commands=[], source_ip="172.30.0.3", domain_gateway=helper)


@pytest.mark.parametrize("stage,code", [
    ("dns_socket_bind", "address_in_use"), ("dns_nft_install", "nft_syntax"),
    ("dns_nft_heartbeat", "permission_denied"), ("dns_health_write", "no_space_left"),
])
def test_helper_startup_failure_reports_exact_stage_and_closes_sockets(monkeypatch, capsys, stage, code):
    import errno

    import dns_mediator
    sockets = [Mock(), Mock()]
    monkeypatch.setattr(dns_mediator.socket, "socket", Mock(side_effect=sockets))
    binder = Mock()
    med = Mediator(config(), binder=binder)
    med.mark_healthy = Mock()
    if stage == "dns_socket_bind":
        sockets[0].bind.side_effect = OSError(errno.EADDRINUSE, "secret-listener")
    elif stage == "dns_nft_install":
        binder.install.side_effect = dns_mediator.GatewayFailure(code)
    elif stage == "dns_nft_heartbeat":
        binder.heartbeat.side_effect = PermissionError(errno.EPERM, "secret-kernel")
    else:
        med.mark_healthy.side_effect = OSError(errno.ENOSPC, "secret-health-path")
    with pytest.raises((PolicyError, OSError)):
        med.serve()
    output = capsys.readouterr()
    assert json.loads(output.out) == {"aw_dns_failure": 1, "execution_id": EID, "stage": stage, "code": code}
    assert "secret" not in output.out and not output.err
    assert med.dead.is_set()
    for sock in sockets:
        sock.close.assert_called_once()
    assert "aw_dns_ready" not in output.out


@pytest.mark.parametrize("kind,code", [("missing", "missing_executable"), ("timeout", "operation_timeout")])
def test_nft_spawn_and_timeout_errors_remain_finite(monkeypatch, kind, code):
    import subprocess

    import dns_mediator
    failure = FileNotFoundError(2, "secret-nft-path") if kind == "missing" else subprocess.TimeoutExpired("secret-argv", 0.5)
    execute = Mock(side_effect=failure)
    monkeypatch.setattr(dns_mediator.subprocess, "run", execute)
    with pytest.raises(PolicyError) as raised:
        dns_mediator.nft("secret-rules")
    assert raised.value.code == code
    assert "secret" not in str(raised.value)
    assert execute.call_args.kwargs["timeout"] == 0.5


@pytest.mark.parametrize("raw", [
    b'{"aw_dns_failure":', b"{" + b"secret" * 30000,
    json.dumps({"aw_dns_failure": 1, "execution_id": EID, "stage": "secret-stage", "code": "nft_syntax"}).encode(),
    json.dumps({"aw_dns_failure": 1, "execution_id": "another-execution", "stage": "dns_nft_install", "code": "nft_syntax"}).encode(),
    json.dumps({"aw_dns_failure": 1, "execution_id": EID, "stage": "dns_nft_install", "code": "secret-code"}).encode(),
])
def test_untrusted_or_malformed_diagnostic_fields_are_not_reflected(raw):
    from app.executors.egress_network import NetworkPolicyError
    helper = Mock()
    helper.logs.return_value = raw
    capture = diagnostic_capture(helper)
    with pytest.raises(NetworkPolicyError, match=r"dns_failure\[dns_startup,diagnostic_invalid\]") as raised:
        capture._gateway_records()
    assert "secret" not in str(raised.value)


def test_failed_health_preserves_kernel_failure_instead_of_generic_staleness():
    from app.executors.egress_network import NetworkPolicyError
    helper = Mock(attrs={"State": {"Running": True}})
    helper.exec_run.return_value = SimpleNamespace(exit_code=1, output=b"secret-health-output")
    helper.logs.return_value = json.dumps({"aw_dns_failure": 1, "execution_id": EID,
                                          "stage": "dns_nft_heartbeat", "code": "operation_timeout"}).encode()
    capture = diagnostic_capture(helper)
    with pytest.raises(NetworkPolicyError, match=r"dns_failure\[dns_nft_heartbeat,operation_timeout\]"):
        capture.assert_healthy()


def test_dns_upstream_timeout_is_distinct_from_kernel_transaction_timeout(monkeypatch):
    from unittest.mock import MagicMock

    import dns_mediator

    attempts = [MagicMock() for _ in dns_mediator.DNS_ATTEMPTS]
    for conn in attempts:
        conn.__enter__.return_value = conn
        conn.recv.side_effect = TimeoutError("SECRET upstream")
    monkeypatch.setattr(dns_mediator.socket, "socket", Mock(side_effect=attempts))
    med, _calls = mediator()
    with pytest.raises(dns_mediator.GatewayFailure) as error:
        med.resolve(Question(1, "www.example.com", 1),
                    dns_mediator.time.monotonic() + dns_mediator.QUERY_DEADLINE_S)
    assert error.value.code == "upstream_timeout"
    assert "SECRET" not in str(error.value)
    # Every attempt is bounded by its own cap and by the query deadline.
    for conn, (_stream, cap) in zip(attempts, dns_mediator.DNS_ATTEMPTS):
        assert conn.settimeout.call_args.args[0] <= cap


def test_query_budget_survives_a_slow_resolver_without_waiting_forever():
    """A cold lookup on a blocked UDP/53 host may take seconds; not minutes."""
    import dns_mediator

    assert dns_mediator.QUERY_DEADLINE_S >= 5
    caps = [cap for _stream, cap in dns_mediator.DNS_ATTEMPTS]
    assert all(cap > 0 for cap in caps)
    # The plan must fit inside the budget it is allowed to spend.
    assert sum(caps) <= dns_mediator.QUERY_DEADLINE_S
    # It must still try UDP first and fall back to TCP, as before.
    assert dns_mediator.DNS_ATTEMPTS[0][0] is False
    assert dns_mediator.DNS_ATTEMPTS[1][0] is True


def test_a_dropped_first_query_is_retried_within_the_same_deadline(monkeypatch):
    """One lost datagram must not abort the execution that needed the name."""
    from unittest.mock import MagicMock

    import dns_mediator

    q = Question(1, "www.example.com", 1)
    payload = response(q, [Record("www.example.com", 1, 30, "93.184.216.34")])
    # UDP drop, TCP drop, then the retry UDP that answers: the retry is what
    # turns a transient into a served query instead of a dead execution.
    dropped_udp, dropped_tcp, answered = MagicMock(), MagicMock(), MagicMock()
    for conn in (dropped_udp, dropped_tcp, answered):
        conn.__enter__.return_value = conn
    dropped_udp.recv.side_effect = TimeoutError("SECRET upstream")
    dropped_tcp.recv.side_effect = TimeoutError("SECRET upstream")
    answered.recv.return_value = payload
    monkeypatch.setattr(dns_mediator.socket, "socket",
                        Mock(side_effect=[dropped_udp, dropped_tcp, answered]))
    med, _calls = mediator()
    assert med.resolve(q, dns_mediator.time.monotonic()
                       + dns_mediator.QUERY_DEADLINE_S) == payload


def test_diagnostic_schemas_match_and_public_extractor_returns_only_finite_tokens():
    import dns_mediator

    from app.executors import egress_network
    assert dns_mediator.DIAGNOSTIC_STAGES == egress_network._DNS_DIAGNOSTIC_STAGES
    assert dns_mediator.DIAGNOSTIC_CODES == egress_network._DNS_DIAGNOSTIC_CODES
    for stage in dns_mediator.DIAGNOSTIC_STAGES:
        for code in dns_mediator.DIAGNOSTIC_CODES:
            error = egress_network.NetworkCapture._dns_error(stage, code)
            assert egress_network.finite_error_token(error) == f"dns_failure[{stage},{code}]"
    for error in (RuntimeError("dns_failure[dns_nft_install,nft_syntax]"),
                  egress_network.NetworkPolicyError("secret-error"),
                  egress_network.NetworkPolicyError("dns_failure[secret-stage,nft_syntax]"),
                  egress_network.NetworkPolicyError("dns_failure[dns_nft_install,nft_syntax] secret-tail")):
        assert egress_network.finite_error_token(error) is None


@pytest.mark.parametrize("status,stats,code", [
    (1, b"secret-output", "capture_exited"),
    (0, b"secret-output", "capture_incomplete"),
    (0, b"0 packets dropped by kernel\n1 packets captured\n", "capture_truncated"),
])
def test_packet_audit_failure_is_distinct_from_gateway_health(status, stats, code):
    from app.executors.egress_network import NetworkPolicyError
    capture = diagnostic_capture(Mock())
    watcher = Mock()
    watcher.wait.return_value = {"StatusCode": status}
    watcher.logs.side_effect = lambda **kwargs: stats if kwargs.get("stderr") else b""
    capture.watcher = watcher
    with pytest.raises(NetworkPolicyError, match=rf"dns_failure\[packet_audit,{code}\]") as raised:
        capture.collect()
    assert "secret" not in str(raised.value)
    watcher.remove.assert_called_once_with(force=True)
    assert capture.watcher is None


def test_network_install_uses_one_restore_process_per_ip_family(monkeypatch):
    import base64

    import command_batch

    commands = [[family, "-N", "AWF123"] for family in ("iptables", "ip6tables")]
    commands += [[family, "-A", "AWF123", "-p", "tcp", "--dport", str(port), "-j", "DROP"]
                 for family in ("iptables", "ip6tables") for port in range(100, 120)]
    commands += [[family, "-I", "FORWARD", "1", "-i", "br-abc123", "-j", "AWF123"]
                 for family in ("iptables", "ip6tables")]
    data = {"execution_id": EID, "commands": commands, "nft_script": "table inet fixed {}"}
    monkeypatch.setattr(command_batch.sys, "argv", ["command_batch.py", base64.b64encode(json.dumps(data).encode()).decode()])
    execute = Mock()
    monkeypatch.setattr(command_batch.subprocess, "run", execute)
    command_batch.main()
    assert execute.call_count == 3  # protocol guard + one transaction per family
    for call, family in zip(execute.call_args_list[1:], ("iptables", "ip6tables"), strict=True):
        assert call.args[0] == [family + "-restore", "--noflush", "--wait", "2"]
        script = call.kwargs["input"].decode()
        assert script.startswith("*filter\n:AWF123 - [0:0]\n")
        assert script.endswith("-I FORWARD 1 -i br-abc123 -j AWF123\nCOMMIT\n")
        assert script.count("--dport") == 20


@pytest.mark.parametrize("commands", [
    [["iptables", "-D", "FORWARD", "-j", "AWF123"]],
    [["nft", "delete", "table", "inet", "awp_owned"]],
    [["iptables", "-A", "AWF123", "--comment", "two words", "-j", "DROP"]],
    [["iptables", "-A", "AWF123", "--comment", "x\nCOMMIT\n*filter", "-j", "DROP"]],
])
def test_restore_does_not_reinterpret_cleanup_or_non_simple_argv(commands):
    import command_batch

    assert command_batch._install_transactions(commands) == [(command, None) for command in commands]


def test_best_effort_preserves_individual_cleanup_failure_count(monkeypatch, capsys):
    import base64

    import command_batch

    commands = [["iptables", "-N", "AWF123"], ["ip6tables", "-N", "AWF123"]]
    data = {"execution_id": EID, "commands": commands, "best_effort": True}
    monkeypatch.setattr(command_batch.sys, "argv", ["command_batch.py", base64.b64encode(json.dumps(data).encode()).decode()])
    execute = Mock(side_effect=[command_batch.subprocess.CalledProcessError(1, commands[0]), None])
    monkeypatch.setattr(command_batch.subprocess, "run", execute)
    command_batch.main()
    assert [call.args[0] for call in execute.call_args_list] == commands
    assert json.loads(capsys.readouterr().out) == {"commands": 2, "failures": 1}


def test_restore_failure_stops_before_other_family_and_reports_finite_code(monkeypatch, capsys):
    import base64

    import command_batch

    commands = [["iptables", "-N", "AWF123"], ["ip6tables", "-N", "AWF123"]]
    data = {"execution_id": EID, "commands": commands}
    monkeypatch.setattr(command_batch.sys, "argv", ["command_batch.py", base64.b64encode(json.dumps(data).encode()).decode()])
    failure = command_batch.subprocess.CalledProcessError(1, ["iptables-restore"], stderr=b"permission denied SECRET")
    execute = Mock(side_effect=failure)
    monkeypatch.setattr(command_batch.subprocess, "run", execute)
    with pytest.raises(command_batch.subprocess.CalledProcessError):
        command_batch.main()
    assert execute.call_count == 1
    output = capsys.readouterr().err
    assert json.loads(output)["code"] == "permission_denied"
    assert "SECRET" not in output


def test_network_batch_failure_reports_only_finite_execution_bound_diagnostic(monkeypatch, capsys):
    import command_batch

    data = {"execution_id": EID, "commands": [], "best_effort": False}
    failure = command_batch.subprocess.TimeoutExpired(["nft", "-f", "-"], 1)
    monkeypatch.setattr(command_batch.subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(command_batch.subprocess.TimeoutExpired):
        command_batch._run_checked(["nft", "-f", "-"], input_data=b"secret-rules",
                                   timeout=1, data=data, stage="policy_install")
    output = capsys.readouterr().err
    assert json.loads(output) == {
        "aw_net_failure": 1, "execution_id": EID,
        "stage": "policy_install", "code": "operation_timeout",
    }
    assert "secret" not in output


def test_network_failure_decoder_rejects_unbound_or_malformed_helper_output():
    from app.executors.egress_network import _finite_network_failure

    valid = json.dumps({"aw_net_failure": 1, "execution_id": EID,
                        "stage": "policy_install", "code": "nft_syntax"})
    assert _finite_network_failure(valid, EID) == "network_failure[policy_install,nft_syntax]"
    assert _finite_network_failure(iter([valid.encode()]), EID) == "network_failure[policy_install,nft_syntax]"
    assert _finite_network_failure(valid.replace(EID, "other"), EID) is None
    assert _finite_network_failure(valid.replace("nft_syntax", "secret"), EID) is None
