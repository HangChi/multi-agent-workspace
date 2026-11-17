"""出站策略 DTO。

`EgressRuleIn` 承担 data-model 不变量 6 的校验：`target_type` 与 `target` 的字面
必须自洽（cidr 能被解析为网段、ip 是合法地址、domain 是合法域名模式）。
放在 DTO 层而非 DB 层，是因为这些错误应当是 400 + 可读中文提示，而不是 500。
"""

import ipaddress
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 域名：允许字母数字、连字符、点；可选前置 `*.` 通配
_DOMAIN_RE = re.compile(
    r"^(\*\.)?([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$"
)
# 端口：单个端口或闭区间
_PORT_RANGE_RE = re.compile(r"^\d{1,5}(-\d{1,5})?$")


def _check_port_range(value: str | None) -> str | None:
    """端口范围校验：空 = 全部端口；`443` / `8000-8100`，且两端都在 1..65535。"""
    if value is None or value == "":
        return None
    if not _PORT_RANGE_RE.match(value):
        raise ValueError(f"端口范围格式非法: {value}（应为 443 或 8000-8100）")
    parts = [int(p) for p in value.split("-")]
    for port in parts:
        if not 1 <= port <= 65535:
            raise ValueError(f"端口超出范围 1-65535: {port}")
    if len(parts) == 2 and parts[0] > parts[1]:
        raise ValueError(f"端口区间起点大于终点: {value}")
    return value


class EgressRuleIn(BaseModel):
    """新增一条出站规则。

    全局黑名单只允许 deny（管理员维护）；Agent 级只允许 allow。
    `scope` 由调用方按端点决定，不由请求体决定，避免误把 agent 规则当全局规则提交。
    """

    target_type: Literal["domain", "cidr", "ip"]
    target: str = Field(min_length=1, max_length=255)
    port_range: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _validate_target(self) -> "EgressRuleIn":
        target = self.target.strip()
        if self.target_type == "cidr":
            # strict=True：拒绝 `10.0.0.1/8` 这种带主机位的写法。
            # 用 strict=False 会静默归一化成 `10.0.0.0/8`，管理员看到的是自己写的
            # `10.0.0.1/8`，实际拦的却是整个 /8 —— 差之毫厘的误拦比报错更危险。
            try:
                ipaddress.ip_network(target, strict=True)
            except ValueError as exc:
                raise ValueError(
                    f"非法网段: {target}（主机位必须为 0，如 10.0.0.0/8）"
                ) from exc
        elif self.target_type == "ip":
            try:
                ipaddress.ip_address(target)
            except ValueError as exc:
                raise ValueError(f"非法 IP: {target}") from exc
        else:  # domain
            if not _DOMAIN_RE.match(target):
                raise ValueError(
                    f"非法域名: {target}（支持 example.com 与 *.example.com）"
                )
        # 归一化结果必须回写：空串要变成 None，否则 DB 里「空串」与 NULL 两种表示
        # 都会存在，查重与唯一约束都会出岔子
        self.port_range = _check_port_range(self.port_range)
        return self


class EgressRuleOut(BaseModel):
    """出站规则。"""

    id: str
    scope: str
    agent_id: str | None = None
    action: str
    target_type: str
    target: str
    port_range: str | None = None
    note: str | None = None
    enabled: bool
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EgressRuleList(BaseModel):
    """Agent 级规则列表（含该 Agent 当前模式，前端据此决定提示文案）。"""

    items: list[EgressRuleOut]
    egress_mode: str


class BlacklistIn(BaseModel):
    """新增全局黑名单项。只有 deny 一种动作，故无 action 字段。"""

    target_type: Literal["domain", "cidr", "ip"]
    target: str = Field(min_length=1, max_length=255)
    port_range: str | None = Field(default=None, max_length=20)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _validate_target(self) -> "BlacklistIn":
        # 与 EgressRuleIn 共用同一套校验，避免两处漂移
        EgressRuleIn(
            target_type=self.target_type, target=self.target, port_range=self.port_range
        )
        return self


class BlacklistOut(BaseModel):
    """全局黑名单项。

    `builtin` 标记来自内置常量（`EGRESS_BUILTIN_BLACKLIST`），这类项不可删除——
    它们是内网与云元数据地址，删掉等于把 SSRF 面打开。
    """

    id: str
    target_type: str
    target: str
    port_range: str | None = None
    note: str | None = None
    enabled: bool
    builtin: bool = False
    created_by: str | None = None
    created_at: datetime | None = None


class BlacklistList(BaseModel):
    """全局黑名单响应。内置项与自定义项合并返回，用 `builtin` 区分。"""

    items: list[BlacklistOut]
