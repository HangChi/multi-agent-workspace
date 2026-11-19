"""egress 判定引擎的边缘单测（015 覆盖率补齐）。

`evaluate()` 已有 21 个用例覆盖四层判定，但真正容易出错的是它下面的匹配原语：

- 端口范围写反（`8080-80`）要不要自动纠正？
- 通配域名 `*.example.com` 是否**包含** `example.com` 本身？
- IPv4-mapped IPv6（`::ffff:1.2.3.4`）能不能命中 IPv4 网段？
- 非法目标地址是放行还是拒绝？

最后一条尤其重要：解析不出的地址**必须 deny**（fail closed），否则构造畸形地址
就能绕过所有规则。端口范围的非法值则是另一回事——按注释的取舍退化为「全部端口」，
因为规则由人工录入，写错一条不该让整个求值崩掉。
"""

import ipaddress

import pytest

from app.executors.egress import (
    RuleView,
    _cidr_matches,
    _domain_matches,
    _ip_matches,
    _parse_port_range,
    _port_matches,
    evaluate,
    rule_view_from_row,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("443", (443, 443)),
        ("8000-8100", (8000, 8100)),
        ("8100-8000", (8000, 8100)),  # 写反自动纠正
        ("abc", None),  # 非法 → None（视为全部端口，见模块说明）
        ("0", (0, 0)),
    ],
)
def test_parse_port_range(raw, expected):
    assert _parse_port_range(raw) == expected


def test_port_matches_semantics():
    assert _port_matches(None, 443) is True  # 空 = 全部端口
    assert _port_matches("443", 443) is True
    assert _port_matches("443", 444) is False
    assert _port_matches("8000-8100", 8050) is True
    assert _port_matches("8000-8100", 8101) is False


@pytest.mark.parametrize(
    ("pattern", "domain", "expected"),
    [
        ("example.com", "example.com", True),
        ("example.com", "api.example.com", False),  # 精确匹配不含子域
        ("*.example.com", "api.example.com", True),
        ("*.example.com", "example.com", True),  # 通配也含裸域
        ("*.example.com", "notexample.com", False),  # 不能被后缀骗过
        ("example.com", None, False),
        ("", "example.com", False),
    ],
)
def test_domain_matches(pattern, domain, expected):
    assert _domain_matches(pattern, domain) is expected


def test_cidr_matches_versions_and_mapped_addresses():
    v4 = ipaddress.ip_address("1.2.3.4")
    assert _cidr_matches("1.2.3.0/24", v4) is True
    assert _cidr_matches("1.2.4.0/24", v4) is False
    # IPv4-mapped IPv6 要按映射后的 v4 判断
    mapped = ipaddress.ip_address("::ffff:1.2.3.4")
    assert _cidr_matches("1.2.3.0/24", mapped) is True
    # 版本不同且非映射 → 不匹配
    v6 = ipaddress.ip_address("2001:db8::1")
    assert _cidr_matches("1.2.3.0/24", v6) is False
    # 非法 CIDR → 不匹配（不抛）
    assert _cidr_matches("not-a-cidr", v4) is False


def test_ip_matches_exact_only():
    ip = ipaddress.ip_address("8.8.8.8")
    assert _ip_matches("8.8.8.8", ip) is True
    assert _ip_matches(" 8.8.8.8 ", ip) is True  # 容忍空白
    assert _ip_matches("8.8.4.4", ip) is False
    assert _ip_matches("garbage", ip) is False  # 非法目标不抛异常


def test_evaluate_denies_unparsable_target():
    """畸形地址必须 deny：放行等于给所有规则开口子。"""
    decision = evaluate("not-an-ip", None, 443)
    assert decision.action == "deny"
    assert "无法解析" in decision.reason


def test_evaluate_builtin_blacklist_hit_has_no_rule_id():
    """内置黑名单不可被任何配置放宽，所以没有「命中哪条规则」这回事。"""
    decision = evaluate("10.0.0.5", None, 80, builtin_blacklist=["10.0.0.0/8"])
    assert decision.action == "deny"
    assert decision.matched_rule_id is None
    assert "内置黑名单" in decision.reason


def test_evaluate_global_deny_reports_rule():
    rule = RuleView(
        id="deny-1", action="deny", target_type="domain", target="*.evil.test"
    )
    decision = evaluate("93.184.216.34", "api.evil.test", 443, global_rules=[rule])
    assert decision.action == "deny"
    assert decision.matched_rule_id == "deny-1"


def test_evaluate_restricted_allows_only_listed():
    allow = RuleView(
        id="a1", action="allow", target_type="cidr", target="93.184.216.0/24"
    )
    allowed = evaluate(
        "93.184.216.34", None, 443, mode="restricted", agent_rules=[allow]
    )
    blocked = evaluate("1.1.1.1", None, 443, mode="restricted", agent_rules=[allow])

    assert allowed.action == "allow"
    assert allowed.matched_rule_id == "a1"
    assert blocked.action == "deny"
    assert "不在允许清单" in blocked.reason


def test_evaluate_default_mode_allows_after_blacklist():
    decision = evaluate("93.184.216.34", None, 443, mode="default")
    assert decision.action == "allow"


def test_rule_view_from_row_accepts_dict_and_object():
    from types import SimpleNamespace

    from_row = rule_view_from_row(
        {"id": "r", "action": "allow", "target_type": "ip", "target": "1.2.3.4"}
    )
    from_obj = rule_view_from_row(
        SimpleNamespace(
            id="r2", action="deny", target_type="ip", target="5.6.7.8", port_range="80"
        )
    )

    assert from_row.target == "1.2.3.4"
    assert from_obj.port_range == "80"
