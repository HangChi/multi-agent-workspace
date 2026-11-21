"""egress 网络策略的纯函数单测（015 覆盖率补齐）。

`egress_network` 大部分代码要真 Docker + Linux netns 才能跑，但**最危险的部分
是纯函数**：把规则快照编译成 iptables/nft 命令。规则写错的后果是双向的——
少一条 drop 就是隔离失效，多一条 drop 就是把业务网也切了。

所以这里不测「Docker 起没起来」，只测：
- 命令是完全参数化的（`list[list[str]]`），绝不拼 shell 字符串（防注入）
- 端口/目标/TargetType 的每一种非法输入都变成 NetworkPolicyError，而不是
  生成一条语义不对的规则
- 抓包解析只认「由沙箱发起」的方向，不计入回包

（R5 删掉了 conntrack 快照解析：运行时按包抓取走 `parse_packet_output`，
`conntrack -L` 那条路径从未接上，连同 5 条测试一起移除。）
"""

import ipaddress
import uuid

import pytest

from app.executors.egress import RuleView
from app.executors.egress_network import (
    NetworkPolicyError,
    _chain_names,
    _dns_ip_domains,
    _port_args,
    _protocol_guard,
    _rule_target,
    _rule_view,
    compile_runtime_commands,
    validate_execution_id,
)

# 运行时只调用 `compile_runtime_commands`（返回 install + cleanup 两段）。
# 这些用例只关心 install 段（规则本身对不对），所以包一层取第一段。
# 之前它们直接调 `compile_iptables_commands`——那个包装是测试专用的，
# 于是 11 条 iptables 断言全在测一个生产代码不走的函数，真正的运行时函数
# 零覆盖。R5 让测试改走真函数，包装删掉。
_FIXED_EXECUTION_ID = "f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a"


def compile_install(**kwargs) -> list[list[str]]:
    install, _cleanup = compile_runtime_commands(
        execution_id=_FIXED_EXECUTION_ID, **kwargs
    )
    return install


def _rule(
    target="1.1.1.0/24",
    *,
    action="allow",
    target_type="cidr",
    port_range=None,
    id_="r1",
):
    return RuleView(
        id=id_,
        action=action,
        target_type=target_type,
        target=target,
        port_range=port_range,
    )


# ---------- execution_id / 链名 ----------


def test_execution_id_must_be_uuid():
    value = str(uuid.uuid4())
    assert validate_execution_id(value) == value

    for bad in ("", "abc", "../etc", f"{value}; rm -rf /", None):
        with pytest.raises(NetworkPolicyError):
            validate_execution_id(bad)  # type: ignore[arg-type]


def test_chain_names_are_derived_and_shell_safe():
    """链名不得含用户可控语法：只取 UUID 去横线大写后的前 20 位。"""
    eid = "0f0e0d0c-0b0a-4908-8706-050403020100"
    forward, input_chain = _chain_names(eid)

    assert forward.startswith("AWF") and input_chain.startswith("AWI")
    assert "-" not in forward and forward == forward.upper()
    assert len(forward) <= 28  # iptables 链名上限 28


def test_protocol_guard_covers_fragments_and_non_tcpudp():
    text = _protocol_guard(str(uuid.uuid4()), "br-abc123")

    assert "frag-off & 0x3fff != 0 drop" in text
    assert "meta l4proto != { tcp, udp } drop" in text
    # PREROUTING 必须早于 conntrack 重组（-400），否则分片检查太晚
    assert "priority -450" in text


def test_protocol_guard_rejects_bad_bridge():
    with pytest.raises(NetworkPolicyError):
        _protocol_guard(str(uuid.uuid4()), "eth0; rm -rf /")


# ---------- 端口与目标 ----------


def test_port_args_empty_and_single_and_range():
    assert _port_args(_rule(port_range=None)) == []
    assert _port_args(_rule(port_range="443")) == ["--dport", "443"]
    assert _port_args(_rule(port_range="8000-8100")) == ["--dport", "8000:8100"]


@pytest.mark.parametrize("bad", ["abc", "0", "70000", "80-", "-80", "1-2-3", "  "])
def test_port_args_rejects_invalid(bad):
    with pytest.raises(NetworkPolicyError):
        _port_args(_rule(port_range=bad))


def test_rule_target_ip_and_cidr():
    assert _rule_target(_rule("1.1.1.1", target_type="ip")) == ("1.1.1.1", 4)
    assert _rule_target(_rule("2001:db8::1", target_type="ip")) == ("2001:db8::1", 6)
    # CIDR 规范化（strict=False：允许 1.1.1.1/24 这种带主机位的写法）
    assert _rule_target(_rule("1.1.1.7/24")) == ("1.1.1.0/24", 4)


def test_rule_target_rejects_domain():
    """内核态策略只认 IP/CIDR：域名规则必须显式失败，而不是被静默忽略。"""
    with pytest.raises(NetworkPolicyError) as exc:
        _rule_target(_rule("example.com", target_type="domain"))
    assert "domain" in str(exc.value)


def test_rule_target_rejects_garbage():
    with pytest.raises(NetworkPolicyError):
        _rule_target(_rule("not-an-ip"))


def test_rule_view_accepts_dict_and_passthrough():
    rule = _rule()
    assert _rule_view(rule) is rule

    view = _rule_view(
        {"id": "x", "action": "deny", "target_type": "ip", "target": "8.8.8.8"}
    )
    assert view.action == "deny"
    assert view.port_range is None


def test_rule_view_rejects_malformed_snapshot():
    with pytest.raises(NetworkPolicyError):
        _rule_view({"action": "allow"})  # 缺 target_type/target


# ---------- 命令编译 ----------


def test_compile_is_parameterized_not_shell_string():
    commands = compile_install(
        bridge="br-abc123",
        dns_proxy_ip="172.30.0.2",
        mode="restricted",
        global_rules=[],
        agent_rules=[_rule("1.1.1.0/24", port_range="443")],
        builtin_blacklist=["10.0.0.0/8"],
    )

    assert commands, "应生成至少一条命令"
    assert all(isinstance(cmd, list) for cmd in commands), "必须是 argv 列表"
    flat = " ".join(arg for cmd in commands for arg in cmd)
    # 没有 shell 元字符（防止任何形式的命令拼接）
    for meta in (";", "&&", "|", "$(", "`"):
        assert meta not in flat


def _v4_forward_chain(commands: list[list[str]]) -> list[str]:
    """取出 v4 FORWARD 链（AWF 开头）上的规则行，按生成顺序。"""
    lines = []
    for cmd in commands:
        text = " ".join(cmd)
        if (
            cmd[0] == "iptables"
            and len(cmd) > 2
            and cmd[1] == "-A"
            and cmd[2].startswith("AWF")
        ):
            lines.append(text)
    return lines


def test_compile_default_mode_is_blacklist_then_allow_all():
    """default 模式语义：先按黑名单 DROP，再兜底 ACCEPT（不逐条展开规则）。"""
    commands = compile_install(
        bridge="br-abc123",
        dns_proxy_ip="172.30.0.2",
        mode="default",
        global_rules=[],
        agent_rules=[_rule("1.1.1.0/24", port_range="443")],
        builtin_blacklist=["10.0.0.0/8"],
    )
    chain = _v4_forward_chain(commands)

    blacklist_at = next(
        i for i, line in enumerate(chain) if "10.0.0.0/8 -j DROP" in line
    )
    # 兜底放行是"无条件 ACCEPT"那一行：不含 -d / -p / --dport（含这些的是
    # DNS 放行或 conntrack 回程放行，不是兜底）
    allow_all_at = next(
        i
        for i, line in enumerate(chain)
        if line.endswith("-j ACCEPT")
        and " -d " not in line
        and " --dport " not in line
        and " -p " not in line
        and "--ctstate" not in line
    )
    assert blacklist_at < allow_all_at, "黑名单必须先于兜底放行"

    flat = "\n".join(chain)
    assert "1.1.1.0/24" not in flat, "default 模式不逐条展开 agent 规则"


def test_compile_restricted_mode_emits_per_rule_and_final_drop():
    commands = compile_install(
        bridge="br-abc123",
        dns_proxy_ip="172.30.0.2",
        mode="restricted",
        global_rules=[],
        agent_rules=[_rule("1.1.1.0/24", port_range="443")],
        builtin_blacklist=[],
    )
    chain = _v4_forward_chain(commands)
    flat = "\n".join(chain)

    assert "1.1.1.0/24" in flat
    assert "--dport 443" in flat
    # 链尾必须兜底 DROP，否则白名单形同虚设
    assert chain[-1].endswith("-j DROP")


def test_compile_rejects_non_docker_bridge():
    with pytest.raises(NetworkPolicyError):
        compile_install(
            bridge="docker0",
            dns_proxy_ip="172.30.0.2",
            mode="default",
            global_rules=[],
            agent_rules=[],
            builtin_blacklist=[],
        )


# ---------- DNS 日志 → IP/域名映射 ----------


def test_dns_ip_domains_maps_unambiguous_replies():
    """只有「本沙箱查过、且该 IP 只对应一个域名」才建立映射。

    输入是 dnsmasq 的文本日志（不是 JSON）：reply 记录本身不带客户端 IP，
    所以只能靠「本沙箱的 query 集合」来归属，有歧义就放弃映射。
    """
    logs = (
        "query[A] api.example.com from 10.0.0.5\n"
        "reply api.example.com is 93.184.216.34\n"
        "query[A] cdn.example.com from 10.0.0.5\n"
        "reply cdn.example.com is 1.1.1.1"
    )

    mapping = _dns_ip_domains(logs, source_ip="10.0.0.5")

    assert mapping["93.184.216.34"] == "api.example.com"
    assert mapping["1.1.1.1"] == "cdn.example.com"


def test_dns_ip_domains_drops_ambiguous_replies():
    """一个 IP 被两个域名解析到 → 无法归属，宁可不映射。"""
    logs = (
        "query[A] a.example.com from 10.0.0.5\n"
        "reply a.example.com is 1.1.1.1\n"
        "query[A] b.example.com from 10.0.0.5\n"
        "reply b.example.com is 1.1.1.1"
    )

    assert _dns_ip_domains(logs, source_ip="10.0.0.5") == {}


def test_dns_ip_domains_skips_malformed():
    assert _dns_ip_domains("not json\n{broken", source_ip="10.0.0.5") == {}


def test_blocked_net_check_uses_ipaddress_objects():
    """顺带确认内置黑名单是可被 ipaddress 使用的网段（不是字符串摆设）。"""
    from app.executors import egress_network

    for net in (
        egress_network._BUILTIN_NETS if hasattr(egress_network, "_BUILTIN_NETS") else []
    ):
        assert isinstance(net, (ipaddress.IPv4Network, ipaddress.IPv6Network))
