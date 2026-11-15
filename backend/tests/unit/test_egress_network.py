from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.executors.egress import RuleView
from app.executors.egress_network import (
    NetworkPolicyError,
    compile_iptables_commands,
    parse_conntrack_output,
    _dns_ip_domains,
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
        language="python", code="print(1)", client=client,
    )
    with pytest.raises(SandboxInfraError, match="missing lookup"):
        run.execute()
    prepare.assert_called_once_with(client, client.network, run.execution_id, None)
    assert client.containers.created == []
    assert client.volumes.created == []
    assert client.network.removed == 1


def test_empty_network_capture_prevents_resource_creation(monkeypatch) -> None:
    from app.core.config import settings
    from app.executors.sandbox_client import SandboxInfraError, SandboxRun
    from tests.sandbox_fixtures import FakeDockerClient

    client = FakeDockerClient()
    client.existing_images = {settings.sbx_image}
    monkeypatch.setattr("app.executors.sandbox_client.NetworkCapture.prepare", Mock(return_value=None))
    run = SandboxRun(
        execution_id="f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a",
        language="python", code="print(1)", client=client,
    )
    with pytest.raises(SandboxInfraError, match="出站网络策略未就绪"):
        run.execute()
    assert client.containers.created == []
    assert client.volumes.created == []
    assert client.network.removed == 1


@pytest.mark.parametrize("reported_ip", ["", "172.30.0.3"])
def test_capture_uses_reserved_address_before_user_process(reported_ip: str) -> None:
    from types import SimpleNamespace

    from app.executors.egress_network import NetworkCapture

    client = Mock()
    client.containers.run.return_value.logs.return_value = b"tcpdump: listening on br-abc123\n"
    capture = NetworkCapture(
        client=client, network=SimpleNamespace(name="sandbox"),
        execution_id="f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a", mode="default",
        global_rules=[], agent_rules=[], builtin_blacklist=[], dns_proxy=Mock(),
        dns_proxy_ip="172.30.0.2", bridge="br-abc123",
        install_commands=[], cleanup_commands=[], source_ip="172.30.0.3",
    )
    container = SimpleNamespace(attrs={"NetworkSettings": {"Networks": {
        "sandbox": {"IPAddress": reported_ip, "IPAMConfig": {"IPv4Address": "172.30.0.3"}},
    }}})
    capture.start_capture(container)
    assert capture.source_ip == "172.30.0.3"
    assert "172.30.0.3" in client.containers.run.call_args.kwargs["command"]


def test_runtime_tags_require_safe_uuid() -> None:
    assert validate_execution_id("f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a")
    for unsafe in ("../evil", "abc;touch /tmp/pwn", "exec-1", "a" * 128):
        with pytest.raises(NetworkPolicyError):
            validate_execution_id(unsafe)


def test_default_policy_is_deny_by_default_but_allows_public_egress_and_proxy_dns() -> None:
    commands = compile_iptables_commands(
        bridge="br-abc123", dns_proxy_ip="172.30.0.2", mode="default",
        global_rules=[], agent_rules=[], builtin_blacklist=["10.0.0.0/8", "::/0"],
    )
    rendered = " ".join(" ".join(command) for command in commands)
    assert "-I FORWARD" in rendered
    assert "-I INPUT" in rendered
    assert "172.30.0.2" in rendered
    assert "10.0.0.0/8" in rendered
    assert "-j DROP" in rendered
    assert "-j ACCEPT" in rendered
    assert "ip6tables" in rendered


def test_restricted_policy_denies_fallback_and_allows_only_ip_or_cidr_allow_rules() -> None:
    rules = [RuleView("r1", "allow", "cidr", "203.0.113.0/24", "443")]
    commands = compile_iptables_commands(
        bridge="br-abc123", dns_proxy_ip="172.30.0.2", mode="restricted",
        global_rules=[], agent_rules=rules, builtin_blacklist=["10.0.0.0/8"],
    )
    rendered = " ".join(" ".join(command) for command in commands)
    assert "203.0.113.0/24" in rendered and "--dport 443" in rendered
    assert "-I FORWARD" in rendered and "-j DROP" in rendered


def test_domain_allow_is_rejected_as_unsupported_not_permitted() -> None:
    with pytest.raises(NetworkPolicyError, match="domain"):
        compile_iptables_commands(
            bridge="br-abc123", dns_proxy_ip="172.30.0.2", mode="restricted",
            global_rules=[],
            agent_rules=[RuleView("r1", "allow", "domain", "*.example.com", "443")],
            builtin_blacklist=["10.0.0.0/8"],
        )


def test_global_deny_cidr_has_kernel_drop_rule() -> None:
    commands = compile_iptables_commands(
        bridge="br-abc123", dns_proxy_ip="172.30.0.2", mode="default",
        global_rules=[RuleView("g1", "deny", "cidr", "198.51.100.0/24", "80-90")],
        agent_rules=[], builtin_blacklist=["10.0.0.0/8"],
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
        ("169.254.169.254", 80, "tcp"), ("172.30.0.2", 53, "udp"),
    ]


def test_dns_proxy_return_path_is_allowed_before_private_drops() -> None:
    commands = compile_iptables_commands(
        bridge="br-abc123", dns_proxy_ip="172.30.0.2", mode="restricted",
        global_rules=[], agent_rules=[], builtin_blacklist=["172.16.0.0/12"],
    )
    rules = [" ".join(command) for command in commands]
    answer = next(i for i, rule in enumerate(rules) if "-s 172.30.0.2" in rule and "--sport 53" in rule)
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


def test_conntrack_parser_records_direct_ip_and_ports() -> None:
    records = parse_conntrack_output(
        "tcp 6 431999 ESTABLISHED src=172.19.0.3 dst=203.0.113.9 sport=42222 dport=8443 packets=3 bytes=256 src=203.0.113.9 dst=172.19.0.3 sport=8443 dport=42222 packets=4 bytes=512\n",
        source_ip="172.19.0.3",
    )
    assert records == [{
        "target_ip": "203.0.113.9", "domain": "ip-direct", "port": 8443,
        "protocol": "tcp", "action": "allow", "matched_rule_id": None,
        "bytes_out": 256,
    }]
