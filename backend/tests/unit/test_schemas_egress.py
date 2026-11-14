"""DTO 校验测试：出站规则的字面合法性。

这些校验在 `schemas/egress.py` 而非 DB 层，是为了让错误变成 400 + 可读中文，
而不是 500。测试重点不是「能通过」，而是**非法输入必须被拒**——
一条写错的 `cidr` 规则若被静默接受，`evaluate()` 里会当成永不匹配，
表现为「明明加了黑名单却没拦住」，这类静默失效最难排查。
"""

import pytest
from pydantic import ValidationError

from app.schemas.egress import BlacklistIn, EgressRuleIn
from app.schemas.tool import ToolOut


class TestTargetValidation:
    """target_type 与 target 必须自洽（data-model 不变量 6）。"""

    @pytest.mark.parametrize(
        "target", ["10.0.0.0/8", "169.254.0.0/16", "192.168.1.0/24", "0.0.0.0/0"]
    )
    def test_valid_cidr(self, target: str) -> None:
        rule = EgressRuleIn(target_type="cidr", target=target)
        assert rule.target == target

    @pytest.mark.parametrize("target", ["10.0.0.1/8", "not-a-cidr", "10.0.0.0/99", "999.0.0.0/8"])
    def test_invalid_cidr_rejected(self, target: str) -> None:
        with pytest.raises(ValidationError):
            EgressRuleIn(target_type="cidr", target=target)

    def test_cidr_with_host_bits_rejected(self) -> None:
        """带主机位的网段必须报错，不能静默归一化。

        静默归一化会让管理员以为只拦了 `10.0.0.1/8` 这一个地址，
        实际却拦了整个 `10.0.0.0/8`——误拦范围被放大且无人知晓。
        """
        with pytest.raises(ValidationError, match="主机位必须为 0"):
            EgressRuleIn(target_type="cidr", target="10.0.0.1/8")

    @pytest.mark.parametrize("target", ["169.254.169.254", "1.1.1.1", "::1"])
    def test_valid_ip(self, target: str) -> None:
        assert EgressRuleIn(target_type="ip", target=target).target == target

    @pytest.mark.parametrize("target", ["10.0.0.0/8", "1.1.1", "abc"])
    def test_invalid_ip_rejected(self, target: str) -> None:
        with pytest.raises(ValidationError):
            EgressRuleIn(target_type="ip", target=target)

    @pytest.mark.parametrize(
        "target", ["pypi.org", "*.pypi.org", "api.example.com", "localhost"]
    )
    def test_valid_domain(self, target: str) -> None:
        assert EgressRuleIn(target_type="domain", target=target).target == target

    @pytest.mark.parametrize("target", ["", " ", "http://pypi.org", "a b.com", "*.", "-x.com"])
    def test_invalid_domain_rejected(self, target: str) -> None:
        with pytest.raises(ValidationError):
            EgressRuleIn(target_type="domain", target=target)


class TestPortRange:
    """端口范围：空 = 全部端口；`443` / `8000-8100`。"""

    @pytest.mark.parametrize("value", [None, "", "443", "8000-8100", "1-65535"])
    def test_valid_port_range(self, value: str | None) -> None:
        rule = EgressRuleIn(target_type="domain", target="pypi.org", port_range=value)
        # 空串归一化为 None，避免 DB 里出现「空字符串」与 NULL 两种表示
        assert rule.port_range == (None if value == "" else value)

    @pytest.mark.parametrize("value", ["abc", "443-", "-443", "0", "65536", "9000-8000", "1-2-3"])
    def test_invalid_port_range_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError):
            EgressRuleIn(target_type="domain", target="pypi.org", port_range=value)


class TestBlacklistIn:
    """黑名单复用同一套校验，避免两处漂移。"""

    def test_shares_validation_with_rule(self) -> None:
        with pytest.raises(ValidationError):
            BlacklistIn(target_type="cidr", target="bad-cidr")

    def test_valid_blacklist(self) -> None:
        item = BlacklistIn(target_type="cidr", target="10.0.0.0/8", note="内网")
        assert item.note == "内网"


class TestToolOut:
    """工具目录 DTO 形状。"""

    def test_roundtrip(self) -> None:
        out = ToolOut(
            name="run_python",
            display_name="运行 Python",
            description="跑脚本",
            kind="sandbox",
            enabled=True,
            param_schema={"type": "object", "properties": {"code": {"type": "string"}}},
            timeout_seconds=120,
        )
        assert out.kind == "sandbox"
        assert out.param_schema["type"] == "object"
