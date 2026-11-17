from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.executors.egress import RuleView
from app.executors.egress_network import (
    NetworkPolicyError,
    _dns_ip_domains,
    compile_iptables_commands,
    parse_conntrack_output,
    validate_execution_id,
)


def test_missing_docker_lookup_does_not_skip_network_policy(monkeypatch) -> None:
    from app.core.config import settings
    from app.executors.sandbox_client import SandboxInfraError, SandboxRun
    from tests.sandbox_fixtures import FakeDockerClient

    client = FakeDockerClient()
    client.existing_images = {settings.sbx_image}
    prepare = Mock(side_effect=NetworkPolicyError("missing lookup"))
    monkeypatch.setattr("app.executors.sandbox_client.NetworkCapture.prepare", prepare)
    run = SandboxRun(
        execution_id="f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a",
        language="python",
        code="print(1)",
        client=client,
    )
    with pytest.raises(SandboxInfraError, match="missing lookup"):
        run.execute()
    prepare.assert_called_once_with(client, client.network, run.execution_id, None)
    assert all(
        container.created_kwargs.get("labels", {}).get("aw.role") == "ipam-lock"
        and not container.started
        and container.removed == [True]
        for container in client.containers.created
    )
    assert client.volumes.created == []
    assert client.network.removed == 1


def test_empty_network_capture_prevents_resource_creation(monkeypatch) -> None:
    from app.core.config import settings
    from app.executors.sandbox_client import SandboxInfraError, SandboxRun
    from tests.sandbox_fixtures import FakeDockerClient

    client = FakeDockerClient()
    client.existing_images = {settings.sbx_image}
    monkeypatch.setattr(
        "app.executors.sandbox_client.NetworkCapture.prepare", Mock(return_value=None)
    )
    run = SandboxRun(
        execution_id="f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a",
        language="python",
        code="print(1)",
        client=client,
    )
    with pytest.raises(SandboxInfraError, match="出站网络策略未就绪"):
        run.execute()
    assert all(
        container.created_kwargs.get("labels", {}).get("aw.role") == "ipam-lock"
        and not container.started
        and container.removed == [True]
        for container in client.containers.created
    )
    assert client.volumes.created == []
    assert client.network.removed == 1


@pytest.mark.parametrize("reported_ip", ["", "172.30.0.3"])
def test_capture_uses_reserved_address_before_user_process(reported_ip: str) -> None:
    from types import SimpleNamespace

    from app.executors.egress_network import NetworkCapture

    client = Mock()
    client.containers.run.return_value.logs.return_value = (
        b"tcpdump: listening on br-abc123\n"
    )
    capture = NetworkCapture(
        client=client,
        network=SimpleNamespace(name="sandbox"),
        execution_id="f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a",
        mode="default",
        global_rules=[],
        agent_rules=[],
        builtin_blacklist=[],
        dns_proxy=Mock(),
        dns_proxy_ip="172.30.0.2",
        bridge="br-abc123",
        install_commands=[],
        cleanup_commands=[],
        source_ip="172.30.0.3",
    )
    container = SimpleNamespace(
        attrs={
            "NetworkSettings": {
                "Networks": {
                    "sandbox": {
                        "IPAddress": reported_ip,
                        "IPAMConfig": {"IPv4Address": "172.30.0.3"},
                    },
                }
            }
        }
    )
    capture.start_capture(container)
    assert capture.source_ip == "172.30.0.3"
    kwargs = client.containers.run.call_args.kwargs
    assert "172.30.0.3" in kwargs["command"]
    assert kwargs["mem_limit"] == "64m"
    assert kwargs["pids_limit"] == 16
    assert kwargs["read_only"] is True
    assert kwargs["log_config"]["Config"] == {
        "max-size": "4m",
        "max-file": "1",
        "compress": "false",
    }


def test_runtime_tags_require_safe_uuid() -> None:
    assert validate_execution_id("f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a")
    for unsafe in ("../evil", "abc;touch /tmp/pwn", "exec-1", "a" * 128):
        with pytest.raises(NetworkPolicyError):
            validate_execution_id(unsafe)


def test_default_policy_is_deny_by_default_but_allows_public_egress_and_proxy_dns() -> (
    None
):
    commands = compile_iptables_commands(
        bridge="br-abc123",
        dns_proxy_ip="172.30.0.2",
        mode="default",
        global_rules=[],
        agent_rules=[],
        builtin_blacklist=["10.0.0.0/8", "::/0"],
    )
    rendered = " ".join(" ".join(command) for command in commands)
    assert "-I FORWARD" in rendered
    assert "-I INPUT" in rendered
    assert "172.30.0.2" in rendered
    assert "10.0.0.0/8" in rendered
    assert "-j DROP" in rendered
    assert "-j ACCEPT" in rendered
    assert "ip6tables" in rendered


def test_restricted_policy_denies_fallback_and_allows_only_ip_or_cidr_allow_rules() -> (
    None
):
    rules = [RuleView("r1", "allow", "cidr", "203.0.113.0/24", "443")]
    commands = compile_iptables_commands(
        bridge="br-abc123",
        dns_proxy_ip="172.30.0.2",
        mode="restricted",
        global_rules=[],
        agent_rules=rules,
        builtin_blacklist=["10.0.0.0/8"],
    )
    rendered = " ".join(" ".join(command) for command in commands)
    assert "203.0.113.0/24" in rendered and "--dport 443" in rendered
    assert "-I FORWARD" in rendered and "-j DROP" in rendered


def test_domain_allow_is_rejected_as_unsupported_not_permitted() -> None:
    with pytest.raises(NetworkPolicyError, match="domain"):
        compile_iptables_commands(
            bridge="br-abc123",
            dns_proxy_ip="172.30.0.2",
            mode="restricted",
            global_rules=[],
            agent_rules=[RuleView("r1", "allow", "domain", "*.example.com", "443")],
            builtin_blacklist=["10.0.0.0/8"],
        )


def test_global_deny_cidr_has_kernel_drop_rule() -> None:
    commands = compile_iptables_commands(
        bridge="br-abc123",
        dns_proxy_ip="172.30.0.2",
        mode="default",
        global_rules=[RuleView("g1", "deny", "cidr", "198.51.100.0/24", "80-90")],
        agent_rules=[],
        builtin_blacklist=["10.0.0.0/8"],
    )
    rendered = " ".join(" ".join(command) for command in commands)
    assert "198.51.100.0/24" in rendered and "--dport 80:90" in rendered


def test_packet_capture_includes_unconfirmed_denied_syn() -> None:
    from app.executors.egress_network import parse_packet_output

    records = parse_packet_output(
        "1763170000.123 IP 172.30.0.3.41234 > 169.254.169.254.80: Flags [S], seq 1, win 64240, length 0\n"
        "1763170000.124 IP 172.30.0.3.41234 > 169.254.169.254.80: Flags [S], seq 1, win 64240, length 0\n"
        "1763170000.125 IP 172.30.0.3.51234 > 172.30.0.2.53: 123+ A? pypi.org. (26)\n",
        source_ip="172.30.0.3",
    )
    assert [(r["target_ip"], r["port"], r["protocol"]) for r in records] == [
        ("169.254.169.254", 80, "tcp"),
        ("172.30.0.2", 53, "udp"),
    ]


def test_ipv6_gateway_neighbor_discovery_precedes_host_input_drop() -> None:
    commands = compile_iptables_commands(
        bridge="br-abc123",
        dns_proxy_ip="172.30.0.2",
        mode="restricted",
        global_rules=[],
        agent_rules=[],
        builtin_blacklist=[],
    )
    input_rules = [
        c
        for c in commands
        if c[0] == "ip6tables" and c[1] == "-A" and c[2].startswith("AWI")
    ]
    assert len(input_rules) == 3
    assert input_rules[-1][-2:] == ["-j", "DROP"]
    for rule, kind in zip(
        input_rules[:2],
        ["neighbor-solicitation", "neighbor-advertisement"],
        strict=True,
    ):
        assert ["--icmpv6-type", kind] == rule[
            rule.index("--icmpv6-type") : rule.index("--icmpv6-type") + 2
        ]
        assert "--hl-eq" in rule and "255" in rule and rule[-1] == "ACCEPT"


def test_dns_proxy_return_path_is_allowed_before_private_drops() -> None:
    commands = compile_iptables_commands(
        bridge="br-abc123",
        dns_proxy_ip="172.30.0.2",
        mode="restricted",
        global_rules=[],
        agent_rules=[],
        builtin_blacklist=["172.16.0.0/12"],
    )
    rules = [" ".join(command) for command in commands]
    answer = next(
        i
        for i, rule in enumerate(rules)
        if "-s 172.30.0.2" in rule and "--sport 53" in rule
    )
    deny = next(i for i, rule in enumerate(rules) if "-d 172.16.0.0/12" in rule)
    assert answer < deny


def test_dns_correlation_requires_unambiguous_matching_reply() -> None:
    logs = """dnsmasq[1]: query[A] example.com from 172.19.0.3
dnsmasq[1]: reply example.com is 203.0.113.9
dnsmasq[1]: query[A] cdn.example from 172.19.0.3
dnsmasq[1]: reply cdn.example is 203.0.113.9
"""
    assert _dns_ip_domains(logs, "172.19.0.3") == {}
    assert _dns_ip_domains(logs, "172.19.0.4") == {}
    assert _dns_ip_domains(
        "dnsmasq[1]: query[A] pypi.org from 172.19.0.3\n"
        "dnsmasq[1]: cached pypi.org is 151.101.0.223\n",
        "172.19.0.3",
    ) == {"151.101.0.223": "pypi.org"}
    assert _dns_ip_domains(
        "dnsmasq[1]: query[A] example.com from 172.19.0.3\n"
        "dnsmasq[1]: reply example.com is 203.0.113.8\n",
        "172.19.0.3",
    ) == {"203.0.113.8": "example.com"}


def test_packet_capture_keeps_distinct_source_ports_and_tcp_generations() -> None:
    from app.executors.egress_network import parse_packet_output

    lines = (
        "1763170000.100 IP 172.30.0.3.41001 > 198.18.0.2.80: Flags [S], seq 100, length 0\n"
        "1763170000.200 IP 172.30.0.3.41001 > 198.18.0.2.80: Flags [S], seq 100, length 0\n"
        "1763170000.300 IP 172.30.0.3.41002 > 198.18.0.2.80: Flags [S], seq 200, length 0\n"
        "1763170000.400 IP 172.30.0.3.41001 > 198.18.0.2.80: Flags [R.], seq 101, length 0\n"
        "1763170001.100 IP 172.30.0.3.41001 > 198.18.0.2.80: Flags [S], seq 300, length 0\n"
    )
    records = parse_packet_output(lines, source_ip="172.30.0.3")
    assert len(records) == 3
    assert [r["source_port"] for r in records] == [41001, 41002, 41001]
    assert [r["occurred_at"] for r in records] == [
        "2025-11-15T01:26:40.100000+00:00",
        "2025-11-15T01:26:40.300000+00:00",
        "2025-11-15T01:26:41.100000+00:00",
    ]


def test_packet_capture_keeps_udp_source_ports_separate() -> None:
    from app.executors.egress_network import parse_packet_output

    records = parse_packet_output(
        "1763170000.1 IP 172.30.0.3.41001 > 198.18.0.2.8000: UDP, length 1\n"
        "1763170000.2 IP 172.30.0.3.41002 > 198.18.0.2.8000: UDP, length 1\n",
        source_ip="172.30.0.3",
    )
    assert len(records) == 2


def test_conntrack_keeps_connections_and_does_not_record_inbound_reply() -> None:
    output = (
        "[NEW] tcp 6 120 SYN_SENT src=172.30.0.3 dst=198.18.0.2 sport=41001 dport=80 id=1\n"
        "[UPDATE] tcp 6 120 ESTABLISHED src=172.30.0.3 dst=198.18.0.2 sport=41001 dport=80 packets=3 bytes=160 id=1\n"
        "[NEW] tcp 6 120 SYN_SENT src=172.30.0.3 dst=198.18.0.2 sport=41002 dport=80 id=2\n"
        "[DESTROY] tcp 6 0 CLOSE src=172.30.0.3 dst=198.18.0.2 sport=41001 dport=80 id=1\n"
        "[NEW] tcp 6 120 SYN_SENT src=172.30.0.3 dst=198.18.0.2 sport=41001 dport=80 id=3\n"
        "tcp 6 120 ESTABLISHED src=198.18.0.2 dst=172.30.0.3 sport=41003 dport=80 src=172.30.0.3 dst=198.18.0.2 sport=80 dport=41003\n"
    )
    records = parse_conntrack_output(output, source_ip="172.30.0.3")
    assert len(records) == 3
    assert [r["source_port"] for r in records] == [41001, 41002, 41001]
    assert records[0]["bytes_out"] == 160


def test_packet_parser_normalizes_equivalent_ipv6_source_addresses() -> None:
    from app.executors.egress_network import parse_packet_output

    records = parse_packet_output(
        "1763170000.1 IP6 2001:db8::3.41001 > 2606:4700::1.443: Flags [S], seq 100, length 0\n",
        source_ip="2001:0db8:0:0:0:0:0:3",
    )
    assert len(records) == 1
    assert records[0]["source_ip"] == "2001:db8::3"


def test_conntrack_ids_remain_distinct_when_events_interleave() -> None:
    records = parse_conntrack_output(
        "[NEW] tcp 6 120 SYN_SENT src=172.30.0.3 dst=198.18.0.2 sport=41001 dport=80 id=1\n"
        "[NEW] tcp 6 120 SYN_SENT src=172.30.0.3 dst=198.18.0.2 sport=41001 dport=80 id=2\n"
        "[DESTROY] tcp 6 0 CLOSE src=172.30.0.3 dst=198.18.0.2 sport=41001 dport=80 packets=3 bytes=160 id=1\n",
        source_ip="172.30.0.3",
    )
    assert len(records) == 2
    assert records[0]["bytes_out"] == 160


def test_packet_parser_uses_actual_ipv6_source_not_ipv4_source() -> None:
    from app.executors.egress_network import parse_packet_output

    records = parse_packet_output(
        "1763170000.1 IP6 2001:db8::3.41001 > 2606:4700::1.443: Flags [S], seq 100, length 0\n"
        "1763170000.2 IP6 2001:db8::2.41001 > 2606:4700::1.443: Flags [S], seq 200, length 0\n",
        source_ip="2001:db8::3",
    )
    assert len(records) == 1
    assert records[0]["source_ip"] == "2001:db8::3"
    assert records[0]["target_ip"] == "2606:4700::1"


@pytest.mark.parametrize("mode", ["default", "restricted"])
def test_global_domain_deny_fails_closed_without_install_time_dns(
    mode, monkeypatch
) -> None:
    resolve = Mock(
        side_effect=AssertionError("one-shot resolution is not a domain policy")
    )
    monkeypatch.setattr("socket.getaddrinfo", resolve)
    with pytest.raises(NetworkPolicyError, match="domain"):
        compile_iptables_commands(
            bridge="br-abc123",
            dns_proxy_ip="172.30.0.2",
            mode=mode,
            global_rules=[RuleView("g1", "deny", "domain", "*.example.com")],
            agent_rules=[],
            builtin_blacklist=[],
        )
    resolve.assert_not_called()


def _capture_for_collection(*, ipv6: str | None = None):
    from types import SimpleNamespace

    from app.executors.egress_network import NetworkCapture

    watcher = Mock()
    watcher.wait.return_value = {"StatusCode": 0}
    stderr = b"tcpdump: listening on br-abc123\n2 packets captured\n2 packets received by filter\n0 packets dropped by kernel\n"
    events = (
        b"1763170000.1 IP 172.30.0.3.41001 > 198.18.0.2.80: Flags [S], seq 100, length 0\n"
        b"1763170000.2 IP6 2001:db8::3.41002 > 2606:4700::1.443: Flags [S], seq 200, length 0\n"
    )
    watcher.logs.side_effect = lambda **kwargs: (
        stderr if kwargs.get("stderr") else events
    )
    proxy = Mock()
    proxy.logs.return_value = b""
    capture = NetworkCapture(
        client=Mock(),
        network=SimpleNamespace(name="sandbox"),
        execution_id="f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a",
        mode="default",
        global_rules=[],
        agent_rules=[],
        builtin_blacklist=[],
        dns_proxy=proxy,
        dns_proxy_ip="172.30.0.2",
        bridge="br-abc123",
        install_commands=[],
        cleanup_commands=[],
        source_ip="172.30.0.3",
        watcher=watcher,
    )
    if ipv6:
        capture.source_ipv6 = ipv6
    return capture, watcher


def test_collection_includes_actual_source_ipv6_and_preserves_packet_time() -> None:
    capture, _ = _capture_for_collection(ipv6="2001:db8::3")
    records, denied = capture.collect()
    assert not denied
    assert len(records) == 2
    assert records[1]["target_ip"] == "2606:4700::1"
    assert records[0]["occurred_at"] == "2025-11-15T01:26:40.100000+00:00"


@pytest.mark.parametrize(
    "stderr,exit_status",
    [
        (b"1 packets dropped by kernel\n", 0),
        (b"0 packets dropped by kernel\n2 packets dropped by interface\n", 0),
        (b"permission denied\n", 1),
        (b"tcpdump: listening on br-abc123\n", 0),
    ],
)
def test_collection_refuses_incomplete_capture(stderr, exit_status) -> None:
    capture, watcher = _capture_for_collection()
    watcher.wait.return_value = {"StatusCode": exit_status}
    watcher.logs.side_effect = lambda **kwargs: stderr if kwargs.get("stderr") else b""
    with pytest.raises(NetworkPolicyError, match="capture"):
        capture.collect()
    watcher.remove.assert_called_once_with(force=True)
    assert capture.watcher is None


@pytest.mark.parametrize(
    "stdout",
    [
        b"1763170000.1 IP 172.30.0.3.41001 > 198.18.0.2.80: Flags [S], seq 100, length 0\n",
        b"1763170000.1 IP 172.30.0.3 > 198.18.0.2: ip-proto-17\n",
    ],
)
def test_collection_refuses_truncated_or_unparseable_packet_log(stdout) -> None:
    capture, watcher = _capture_for_collection()
    stderr = b"2 packets captured\n2 packets received by filter\n0 packets dropped by kernel\n"
    watcher.logs.side_effect = lambda **kwargs: (
        stderr if kwargs.get("stderr") else stdout
    )
    with pytest.raises(NetworkPolicyError, match="capture"):
        capture.collect()


def test_capture_ipv6_requires_a_known_prestart_address() -> None:
    from types import SimpleNamespace

    capture, _ = _capture_for_collection()
    capture.network.attrs = {"EnableIPv6": True}
    capture.watcher = None
    capture.client.containers.run.return_value.logs.return_value = (
        b"tcpdump: listening on br-abc123\n"
    )
    container = SimpleNamespace(
        attrs={
            "NetworkSettings": {
                "Networks": {
                    "sandbox": {"IPAddress": "172.30.0.3"},
                }
            }
        }
    )
    with pytest.raises(NetworkPolicyError, match="IPv6"):
        capture.start_capture(container)
    capture.client.containers.run.assert_not_called()


def test_prepare_reserves_ipv6_next_to_dns_proxy() -> None:
    from types import SimpleNamespace

    from app.executors.egress_network import NetworkCapture

    network = SimpleNamespace(
        name="sandbox",
        connect=Mock(),
        disconnect=Mock(),
        reload=Mock(),
        attrs={"Id": "abcdef123456", "EnableIPv6": True},
    )
    proxy = Mock()
    proxy.attrs = {
        "NetworkSettings": {
            "Networks": {
                "sandbox": {
                    "IPAddress": "172.30.0.2",
                    "GlobalIPv6Address": "2001:db8::2",
                }
            }
        }
    }
    client = Mock()
    client.containers.get.return_value = proxy
    client.containers.run.return_value = b""
    capture = NetworkCapture.prepare(
        client,
        network,
        "f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a",
        {},
    )
    assert capture.source_ip == "172.30.0.3"
    assert capture.source_ipv6 == "2001:db8::3"


def test_capture_rejects_a_different_reserved_ipv6_endpoint() -> None:
    from types import SimpleNamespace

    capture, _ = _capture_for_collection(ipv6="2001:db8::3")
    capture.network.attrs = {"EnableIPv6": True}
    capture.watcher = None
    container = SimpleNamespace(
        attrs={
            "NetworkSettings": {
                "Networks": {
                    "sandbox": {
                        "IPAMConfig": {
                            "IPv4Address": "172.30.0.3",
                            "IPv6Address": "2001:db8::4",
                        }
                    },
                }
            }
        }
    )
    with pytest.raises(NetworkPolicyError, match="IPv6"):
        capture.start_capture(container)
    capture.client.containers.run.assert_not_called()


def test_capture_filter_includes_reserved_ipv6_and_absolute_tcp_sequence() -> None:
    from types import SimpleNamespace

    capture, _ = _capture_for_collection()
    capture.network.attrs = {"EnableIPv6": True}
    capture.watcher = None
    capture.client.containers.run.return_value.logs.return_value = (
        b"tcpdump: listening on br-abc123\n"
    )
    container = SimpleNamespace(
        attrs={
            "NetworkSettings": {
                "Networks": {
                    "sandbox": {
                        "IPAMConfig": {
                            "IPv4Address": "172.30.0.3",
                            "IPv6Address": "2001:db8::3",
                        }
                    },
                }
            }
        }
    )
    capture.start_capture(container)
    command = capture.client.containers.run.call_args.kwargs["command"]
    assert "2001:db8::3" in command and "/opt/aw/packet_capture.py" in command
    assert capture.source_ipv6 == "2001:db8::3"


def test_conntrack_parser_records_direct_ip_and_ports() -> None:
    records = parse_conntrack_output(
        "tcp 6 431999 ESTABLISHED src=172.19.0.3 dst=203.0.113.9 sport=42222 dport=8443 packets=3 bytes=256 src=203.0.113.9 dst=172.19.0.3 sport=8443 dport=42222 packets=4 bytes=512\n",
        source_ip="172.19.0.3",
    )
    assert records == [
        {
            "target_ip": "203.0.113.9",
            "domain": "ip-direct",
            "port": 8443,
            "protocol": "tcp",
            "action": "allow",
            "matched_rule_id": None,
            "bytes_out": 256,
            "source_ip": "172.19.0.3",
            "source_port": 42222,
        }
    ]
