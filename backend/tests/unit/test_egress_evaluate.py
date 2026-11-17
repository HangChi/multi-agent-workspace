"""出站策略求值测试（T016）。

**这是安全边界，必须反向验证**：每个断言都要能证明「改坏了会红」。
重点是 FR-022——Agent 级 allow 不能放宽内置黑名单。
"""

import pytest

from app.executors.egress import RuleView, evaluate

BLACKLIST = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "127.0.0.0/8",
]


class TestBuiltinBlacklist:
    """第 1 层：内置黑名单，任何配置都不能放宽（FR-022）。"""

    @pytest.mark.parametrize(
        "ip",
        [
            "10.1.2.3",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.1.1",
            "169.254.169.254",  # 云元数据
            "127.0.0.1",
        ],
    )
    def test_rfc1918_and_metadata_denied(self, ip: str) -> None:
        d = evaluate(ip, None, 80, builtin_blacklist=BLACKLIST)
        assert d.action == "deny"
        assert "内置黑名单" in d.reason

    def test_agent_allow_cannot_override_blacklist(self) -> None:
        """反向验证 FR-022：给足 allow 规则，内网仍然被拦。

        如果把 evaluate 的顺序改成「先看 agent allow」，此测试必红。
        """
        allow_all_internal = [
            RuleView(id="r1", action="allow", target_type="cidr", target="10.0.0.0/8"),
            RuleView(
                id="r2", action="allow", target_type="ip", target="169.254.169.254"
            ),
        ]
        d = evaluate(
            "10.1.2.3",
            None,
            443,
            mode="restricted",
            agent_rules=allow_all_internal,
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "deny"
        assert "内置黑名单" in d.reason

    def test_global_rule_cannot_allow_blacklist(self) -> None:
        """全局规则即使写成 allow，也不能放宽黑名单（scope 本应只允许 deny）。"""
        d = evaluate(
            "169.254.169.254",
            None,
            80,
            global_rules=[
                RuleView(
                    id="g1", action="allow", target_type="cidr", target="169.254.0.0/16"
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "deny"

    def test_public_ip_passes_blacklist(self) -> None:
        d = evaluate("1.1.1.1", None, 443, builtin_blacklist=BLACKLIST)
        assert d.action == "allow"


class TestGlobalDeny:
    def test_global_deny_by_domain(self) -> None:
        d = evaluate(
            "93.184.216.34",
            "evil.example.com",
            443,
            global_rules=[
                RuleView(
                    id="g1",
                    action="deny",
                    target_type="domain",
                    target="evil.example.com",
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "deny"
        assert d.matched_rule_id == "g1"

    def test_global_deny_wildcard(self) -> None:
        d = evaluate(
            "1.2.3.4",
            "a.b.example.com",
            443,
            global_rules=[
                RuleView(
                    id="g1", action="deny", target_type="domain", target="*.example.com"
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "deny"

    def test_global_deny_port_range_miss(self) -> None:
        """端口不匹配则不拦——证明端口维度真的参与判定。"""
        d = evaluate(
            "1.2.3.4",
            "example.com",
            443,
            global_rules=[
                RuleView(
                    id="g1",
                    action="deny",
                    target_type="domain",
                    target="example.com",
                    port_range="8000-8100",
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "allow"

    def test_global_deny_cidr(self) -> None:
        d = evaluate(
            "203.0.113.5",
            None,
            22,
            global_rules=[
                RuleView(
                    id="g1", action="deny", target_type="cidr", target="203.0.113.0/24"
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "deny"


class TestModes:
    def test_default_mode_allows_unlisted(self) -> None:
        d = evaluate(
            "1.2.3.4", "anything.com", 443, mode="default", builtin_blacklist=BLACKLIST
        )
        assert d.action == "allow"
        assert d.matched_rule_id is None

    def test_restricted_denies_without_allow_rule(self) -> None:
        d = evaluate(
            "1.2.3.4",
            "anything.com",
            443,
            mode="restricted",
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "deny"
        assert "限制模式" in d.reason

    def test_restricted_allows_matching_rule(self) -> None:
        d = evaluate(
            "151.101.64.223",
            "pypi.org",
            443,
            mode="restricted",
            agent_rules=[
                RuleView(
                    id="a1", action="allow", target_type="domain", target="*.pypi.org"
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "allow"
        assert d.matched_rule_id == "a1"

    def test_restricted_agent_rules_ignored_in_default_mode(self) -> None:
        """default 模式下 agent 规则不参与——否则「限制模式」就没有意义了。"""
        d = evaluate(
            "1.2.3.4",
            "other.com",
            443,
            mode="default",
            agent_rules=[
                RuleView(
                    id="a1", action="allow", target_type="domain", target="pypi.org"
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "allow"
        assert d.matched_rule_id is None


class TestDomainBindingSafety:
    def test_rebinding_to_private_address_cannot_inherit_domain_allow(self) -> None:
        rule = RuleView("a1", "allow", "domain", "*.example.com", "443")
        for address, expected in [
            ("1.1.1.1", "allow"),
            ("10.1.2.3", "deny"),
            ("::ffff:10.1.2.3", "deny"),
        ]:
            assert (
                evaluate(
                    address,
                    "cdn.example.com",
                    443,
                    mode="restricted",
                    agent_rules=[rule],
                    builtin_blacklist=BLACKLIST,
                ).action
                == expected
            )

    def test_global_domain_deny_wins_over_exact_or_wildcard_allow(self) -> None:
        for allow_target in ["cdn.example.com", "*.example.com"]:
            decision = evaluate(
                "1.1.1.1",
                "cdn.example.com",
                443,
                mode="restricted",
                global_rules=[RuleView("g1", "deny", "domain", "*.example.com")],
                agent_rules=[RuleView("a1", "allow", "domain", allow_target)],
                builtin_blacklist=BLACKLIST,
            )
            assert (decision.action, decision.matched_rule_id) == ("deny", "g1")

    def test_domain_suffix_does_not_match_a_sibling_or_attacker_suffix(self) -> None:
        for domain in ["notexample.com", "example.com.attacker.invalid"]:
            assert (
                evaluate(
                    "1.1.1.1",
                    domain,
                    443,
                    mode="restricted",
                    agent_rules=[RuleView("a1", "allow", "domain", "*.example.com")],
                    builtin_blacklist=BLACKLIST,
                ).action
                == "deny"
            )


class TestDomainMatching:
    def test_wildcard_matches_base_domain_too(self) -> None:
        d = evaluate(
            "1.2.3.4",
            "example.com",
            443,
            global_rules=[
                RuleView(
                    id="g1", action="deny", target_type="domain", target="*.example.com"
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "deny"

    def test_ip_direct_marker_not_matched_as_domain(self) -> None:
        """`ip-direct` 是标记不是域名，不能被域名规则命中。"""
        d = evaluate(
            "1.2.3.4",
            "ip-direct",
            443,
            global_rules=[
                RuleView(
                    id="g1", action="deny", target_type="domain", target="ip-direct"
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "allow"


class TestMalformedInput:
    def test_invalid_ip_denied(self) -> None:
        """解析不出的地址必须 deny——否则畸形输入可绕过所有规则。"""
        d = evaluate("not-an-ip", None, 443, builtin_blacklist=BLACKLIST)
        assert d.action == "deny"
        assert "无法解析" in d.reason

    def test_invalid_rule_cidr_does_not_crash(self) -> None:
        d = evaluate(
            "1.2.3.4",
            None,
            443,
            global_rules=[
                RuleView(
                    id="bad", action="deny", target_type="cidr", target="not-a-cidr"
                )
            ],
            builtin_blacklist=BLACKLIST,
        )
        assert d.action == "allow"

    def test_ipv6_public_allowed(self) -> None:
        d = evaluate("2606:4700::1", None, 443, builtin_blacklist=BLACKLIST)
        assert d.action == "allow"

    def test_ipv4_mapped_ipv6_hits_v4_blacklist(self) -> None:
        """::ffff:10.0.0.1 必须被 10.0.0.0/8 拦住（绕过点）。"""
        d = evaluate("::ffff:10.0.0.1", None, 443, builtin_blacklist=BLACKLIST)
        assert d.action == "deny"
