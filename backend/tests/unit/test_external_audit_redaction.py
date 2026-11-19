"""外部工具审计的脱敏逻辑（015 覆盖率补齐）。

`write_external_audit` 是外部工具链路唯一的审计入口，而它做的事本质上是
**减信息**：把可能含密钥的 detail 递归压成安全摘要。这类代码的 bug 方向
永远是「漏脱敏」——不会抛异常，只会安静地把 Authorization 头写进日志，
然后在下一次排查时被翻出来。

因此这里的断言全部围绕「哪些必须没掉」：
1. 敏感键（按名字模糊匹配）的值一律 `[redacted]`，嵌套多层同样生效；
2. 深度超过 4 层截断——防止恶意深嵌套 payload 把审计表撑爆；
3. 字典键/列表元素各截到 50 个，字符串截到 512 字符；
4. 非标量对象转成 `str()` 且再截断（不能把 ORM 对象整个序列化进去）；
5. action 必须在白名单内——白名单外的动作直接拒绝，避免审计表变成
   任意字符串的垃圾场。
"""

import hashlib
import json

import pytest

from app.services.external_audit_service import (
    EXTERNAL_AUDIT_ACTIONS,
    _safe_value,
    payload_hash,
    write_external_audit,
)


class _FakeSession:
    """只记录 commit/alush 的 session 桩：这里不验 SQL。"""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)


@pytest.mark.parametrize(
    "key",
    [
        "authorization",
        "Authorization",
        "AUTHORIZATION",
        "x-token",
        "client_secret",
        "password",
        "credential_id",
        "api_key",
        "api-key",
    ],
)
def test_sensitive_keys_are_redacted_case_insensitively(key):
    """键名模糊匹配且不分大小写。

    名单里没有 `headers`——它是中性容器；但 headers 里的 `authorization`
    子键会命中（见嵌套用例）。这里验的是「敏感键名本身」这条主干。
    """
    assert _safe_value({"super-secret-value": 1}, key=key) == "[redacted]"


def test_non_sensitive_keys_keep_their_values():
    safe = _safe_value({"tool_name": "lookup", "count": 3}, key="detail")
    assert safe == {"tool_name": "lookup", "count": 3}


def test_redaction_applies_at_every_nesting_level():
    """深层嵌套同样脱敏：攻击者可以把密钥塞在第 3 层字典里。

    `headers` 本身不在敏感名单（它是中性容器），但它下面的
    `authorization` 会命中；`body.api_key` 同理。两者都应整块抹掉，
    而不是留下 `Bearer leaked-token` 这种半截值。
    """
    payload = {
        "request": {
            "headers": {"authorization": "Bearer leaked-token"},
            "body": {"api_key": "sk-leaked"},
        },
        "tool": "lookup",
    }
    safe = _safe_value(payload)
    text = json.dumps(safe, ensure_ascii=False)
    assert "leaked" not in text
    assert safe["request"]["headers"] == {"authorization": "[redacted]"}
    assert safe["request"]["body"]["api_key"] == "[redacted]"
    assert safe["tool"] == "lookup"


def test_depth_beyond_four_is_truncated():
    """超过 4 层直接截断：深嵌套是撑爆审计表的低成本攻击面。"""
    payload: dict = {"v": 1}
    for _ in range(6):
        payload = {"n": payload}
    safe = _safe_value(payload)
    assert "[truncated]" in json.dumps(safe)


def test_lists_and_dicts_are_capped_at_fifty_items():
    """键与元素各截 50：一次调用可能带回上千条 egress 记录。"""
    safe_dict = _safe_value({f"k{i}": i for i in range(80)})
    safe_list = _safe_value(list(range(80)))
    assert len(safe_dict) == 50
    assert len(safe_list) == 50


def test_dict_keys_are_capped_at_eighty_chars():
    """键名本身也截断：超长键能把行存储撑大。"""
    safe = _safe_value({"k" * 200: "v"})
    assert list(safe)[0] == "k" * 80


def test_long_strings_are_capped_at_512_chars():
    safe = _safe_value("x" * 5000)
    assert len(safe) == 512


@pytest.mark.parametrize("value", [None, 0, 1.5, True])
def test_scalars_pass_through_unchanged(value):
    assert _safe_value(value) == value


def test_unknown_objects_become_truncated_strings():
    """ORM 对象/自定义类型不能整个序列化进去——转 str 再截断。"""

    class Weird:
        def __str__(self) -> str:
            return "w" * 500

    assert len(_safe_value(Weird())) == 128


def test_payload_hash_is_stable_and_order_insensitive():
    """摘要用于「同不同」判定，所以键序不能影响结果。"""
    first = payload_hash({"b": 2, "a": 1})
    second = payload_hash({"a": 1, "b": 2})
    assert first == second
    assert (
        first
        == hashlib.sha256(
            json.dumps(
                {"a": 1, "b": 2},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    )


def test_payload_hash_handles_non_serializable_values():
    """`default=str` 兜底：审计不能因为一个 datetime 就整条失败。"""
    digest = payload_hash({"when": object()})
    assert isinstance(digest, str) and len(digest) == 64


@pytest.mark.asyncio
async def test_write_external_audit_rejects_unknown_action():
    """白名单外的 action 直接拒绝：审计表不能变成任意字符串的垃圾场。"""
    with pytest.raises(ValueError, match="unknown external-tool audit action"):
        await write_external_audit(
            _FakeSession(),
            user_id="u",
            action="something.invented",
            target_type="run",
            target_id="r",
            detail={},
        )


@pytest.mark.asyncio
async def test_write_external_audit_redacts_before_persisting(monkeypatch):
    """落库前的 detail 必须已经是脱敏结果（不是落库后再洗）。"""
    captured: dict = {}

    async def _fake_write(_db, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("app.services.external_audit_service.write_audit", _fake_write)

    await write_external_audit(
        _FakeSession(),
        user_id="u1",
        action="external_tool_invocation",
        target_type="external_tool_invocation",
        target_id="inv-1",
        detail={"token": "leaked", "tool": "lookup"},
    )

    assert captured["detail"] == {"token": "[redacted]", "tool": "lookup"}
    assert captured["strict"] is True  # 审计失败必须冒泡，不能静默丢


def test_action_whitelist_covers_every_documented_lifecycle_step():
    """白名单是契约的一部分：删掉任何一条都会让对应操作静默失去审计。"""
    for action in [
        "external_server_register",
        "external_tool_approve",
        "external_tool_grant",
        "external_tool_revoke",
        "external_credential_rotate",
        "external_tool_invocation",
    ]:
        assert action in EXTERNAL_AUDIT_ACTIONS
