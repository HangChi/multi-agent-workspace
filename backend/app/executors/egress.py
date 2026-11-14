"""出站策略求值（research R-02 第 1 层，纯函数无 IO）。

**为什么必须是纯函数**：这是安全边界的判定核心，必须在没有任何外部依赖
（DB / Redis / 网络）的情况下可穷举单测。黑名单求值写错一个分支，
等于把内网暴露给用户脚本——这类错误不能靠集成测试碰运气发现。

求值顺序（固定，不可调整，data-model 不变量 5）：
1. **内置黑名单**（来自 `EGRESS_BUILTIN_BLACKLIST` 配置）：无条件 deny。
   Agent 配置与全局规则都不能放宽它（FR-022）。
2. **全局 deny 规则**（`egress_rules` where scope='global'）。
3. **Agent allow 规则**：仅当 `mode='restricted'` 时参与判定。
4. 兜底：`mode='default'` → allow；`mode='restricted'` → deny。

顺序为什么不能换：如果先看 Agent allow，那么一条 `allow 10.0.0.0/8`
就能覆盖内置黑名单——这正是 FR-022 禁止的。
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["allow", "deny"]
EgressMode = Literal["default", "restricted"]
TargetType = Literal["domain", "cidr", "ip"]

# `8000-8100` / `443` / 空
_PORT_RANGE = re.compile(r"^(\d{1,5})(?:-(\d{1,5}))?$")


@dataclass(frozen=True)
class RuleView:
    """规则的最小视图：只含求值需要的字段。

    刻意不直接用 ORM 对象：`evaluate()` 要保持纯函数，
    传 ORM 对象会隐含「必须有 DB session」的依赖（懒加载会炸）。
    """

    id: str | None
    action: Action
    target_type: TargetType
    target: str
    port_range: str | None = None


@dataclass(frozen=True)
class Decision:
    """求值结果。

    Attributes:
        action: 最终判定。
        matched_rule_id: 命中的规则 id；内置黑名单或兜底放行时为 None。
        reason: 可读原因，直接用于 `error_message` 与 `egress_denied` 事件，
            让用户知道「为什么被拦」，而不是只看到一个 deny。
    """

    action: Action
    matched_rule_id: str | None
    reason: str


def _parse_port_range(raw: str | None) -> tuple[int, int] | None:
    """解析端口范围；空/非法返回 None（视为「全部端口」）。

    非法值不抛异常：规则由人工录入，一条写错的规则不应该让整个执行的
    求值崩掉——宁可退化为「全部端口」并在录入侧校验。
    """
    if not raw:
        return None
    matched = _PORT_RANGE.match(raw.strip())
    if not matched:
        return None
    low = int(matched.group(1))
    high = int(matched.group(2)) if matched.group(2) else low
    if low > high:
        low, high = high, low
    return low, high


def _port_matches(port_range: str | None, port: int) -> bool:
    parsed = _parse_port_range(port_range)
    if parsed is None:
        return True  # 空 = 全部端口
    return parsed[0] <= port <= parsed[1]


def _domain_matches(pattern: str, domain: str | None) -> bool:
    """域名匹配：支持 `*.example.com` 通配。

    `*.example.com` 同时匹配 `example.com` 本身与任意子域。
    理由：写规则的人写 `*.pypi.org` 时，通常也想包含 `pypi.org`；
    少这一条会让人误以为放行了却仍被拦，是更糟的失败模式。
    """
    if not domain:
        return False
    pattern = pattern.strip().lower().rstrip(".")
    domain = domain.strip().lower().rstrip(".")
    if not pattern:
        return False
    if pattern.startswith("*."):
        base = pattern[2:]
        return domain == base or domain.endswith("." + base)
    return domain == pattern


def _cidr_matches(cidr: str, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        network = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        return False
    # 版本不同（v4 规则 vs v6 地址）直接不匹配，避免 TypeError
    if network.version != ip.version:
        # 但 v6 地址可能是 v4-mapped，展开后再比一次
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is None or network.version != mapped.version:
            return False
        ip = mapped
    return ip in network


def _ip_matches(target: str, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        return ipaddress.ip_address(target.strip()) == ip
    except ValueError:
        return False


def _rule_matches(
    rule: RuleView,
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    domain: str | None,
    port: int,
) -> bool:
    """规则是否命中该次连接（三个维度都要满足）。"""
    if not _port_matches(rule.port_range, port):
        return False
    if rule.target_type == "domain":
        return _domain_matches(rule.target, domain)
    if rule.target_type == "cidr":
        return _cidr_matches(rule.target, ip)
    if rule.target_type == "ip":
        return _ip_matches(rule.target, ip)
    return False


def _builtin_blacklist_hit(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    blacklist: list[str],
) -> str | None:
    """内置黑名单命中则返回该网段，否则 None。"""
    for cidr in blacklist:
        if _cidr_matches(cidr, ip):
            return cidr
    return None


def evaluate(
    target_ip: str,
    target_domain: str | None,
    port: int,
    *,
    mode: EgressMode = "default",
    global_rules: list[RuleView] | None = None,
    agent_rules: list[RuleView] | None = None,
    builtin_blacklist: list[str] | None = None,
) -> Decision:
    """求值一次出站连接。

    Args:
        target_ip: 真实连接目标 IP（conntrack 得到的那一个）。
        target_domain: DNS 反查到的域名；反查不到传 None 或 `ip-direct`。
        port: 目标端口。
        mode: Agent 的出站模式。
        global_rules: 全局规则（应只含 deny，但这里不假设，按 action 分流）。
        agent_rules: 该 Agent 的规则（应只含 allow）。
        builtin_blacklist: 内置黑名单网段列表。

    Returns:
        Decision。非法 IP 一律 deny（解析不出的地址不能放行——
        否则构造畸形地址就能绕过所有规则）。
    """
    global_rules = global_rules or []
    agent_rules = agent_rules or []
    builtin_blacklist = builtin_blacklist or []
    # `ip-direct` 是「未反查到域名」的标记，不是真域名，参与域名匹配无意义
    domain = None if target_domain in (None, "", "ip-direct") else target_domain

    try:
        ip = ipaddress.ip_address(target_ip.strip())
    except (ValueError, AttributeError):
        return Decision(
            action="deny",
            matched_rule_id=None,
            reason=f"目标地址无法解析：{target_ip}",
        )

    # ---- 第 1 层：内置黑名单（不可被任何配置放宽，FR-022）----
    hit_cidr = _builtin_blacklist_hit(ip, builtin_blacklist)
    if hit_cidr:
        return Decision(
            action="deny",
            matched_rule_id=None,
            reason=f"目标 {ip} 命中内置黑名单 {hit_cidr}（内网/元数据地址不可访问）",
        )

    # ---- 第 2 层：全局 deny ----
    for rule in global_rules:
        if rule.action == "deny" and _rule_matches(rule, ip, domain, port):
            return Decision(
                action="deny",
                matched_rule_id=rule.id,
                reason=f"目标 {domain or ip}:{port} 命中全局黑名单规则 {rule.target}",
            )

    # ---- 第 3 层：Agent allow（仅 restricted 模式生效）----
    if mode == "restricted":
        for rule in agent_rules:
            if rule.action == "allow" and _rule_matches(rule, ip, domain, port):
                return Decision(
                    action="allow",
                    matched_rule_id=rule.id,
                    reason=f"目标 {domain or ip}:{port} 命中该 Agent 的允许规则 {rule.target}",
                )
        # restricted 且无匹配 allow → deny
        return Decision(
            action="deny",
            matched_rule_id=None,
            reason=f"该 Agent 为限制模式，目标 {domain or ip}:{port} 不在允许清单内",
        )

    # ---- 第 4 层：default 模式兜底放行 ----
    return Decision(
        action="allow",
        matched_rule_id=None,
        reason=f"默认模式放行 {domain or ip}:{port}",
    )


def rule_view_from_row(row: Any) -> RuleView:
    """把 ORM 行 / dict 转成 RuleView（供调用方便捷转换）。"""
    if isinstance(row, dict):
        return RuleView(
            id=row.get("id"),
            action=row["action"],
            target_type=row["target_type"],
            target=row["target"],
            port_range=row.get("port_range"),
        )
    return RuleView(
        id=getattr(row, "id", None),
        action=row.action,
        target_type=row.target_type,
        target=row.target,
        port_range=getattr(row, "port_range", None),
    )
