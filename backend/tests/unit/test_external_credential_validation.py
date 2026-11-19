"""外部工具凭证的输入校验单测（015 覆盖率补齐）。

凭证是「用户把自己的 token 交给平台保管」，所以边界只有两个方向能出错：
- 收得太松：把 `X-Custom-Header` 之类的头名存进库，注入时拼到错误的位置，
  结果是把密钥发给了不该收到的头；
- 收得太严：正常用法被拒。

另外「Bearer 必须用 Authorization 头」不是风格问题——Bearer 是 Authorization
头的固定语法，写到 X-API-Key 里对端根本不会认，属于静默失败。
"""

import pytest

from app.api.errors import AppError
from app.services import external_credential_service as svc


def test_cipher_uses_configured_key_version():
    cipher = svc.credential_cipher()
    assert cipher.key_version == "v1"


@pytest.mark.parametrize(
    ("kind", "header_arg", "expected"),
    [
        ("bearer", None, "authorization"),
        ("api_key", None, "x-api-key"),
        ("api_key", "X-Api-Key", "x-api-key"),
        ("api_key", "AUTHORIZATION", "authorization"),
    ],
)
def test_header_defaults_and_normalization(kind, header_arg, expected):
    assert svc._validate_header(kind, header_arg) == expected


def test_header_whitelist_rejects_custom_header():
    with pytest.raises(AppError) as exc:
        svc._validate_header("api_key", "x-custom-token")
    assert exc.value.code == "INVALID_INPUT"


def test_bearer_must_use_authorization_header():
    """Bearer 写进 X-API-Key 对端不会认：必须当场拒绝，而不是静默失败。"""
    with pytest.raises(AppError) as exc:
        svc._validate_header("bearer", "x-api-key")
    assert exc.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("kind", ["jwt", "", "Bearer", "api-key"])
def test_create_rejects_unknown_kind(kind):
    """kind 只有 bearer / api_key 两种；大小写与连字符变体都不接受。"""
    import asyncio

    with pytest.raises(AppError) as exc:
        asyncio.run(
            svc.create_credential(
                None,  # type: ignore[arg-type] - 校验发生在触库之前
                server_id="s",
                owner_id="o",
                created_by="o",
                label="l",
                kind=kind,
                value="v",
            )
        )
    assert exc.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("value", ["", "x" * 8193])
def test_create_rejects_empty_or_oversized_value(value):
    import asyncio

    with pytest.raises(AppError) as exc:
        asyncio.run(
            svc.create_credential(
                None,  # type: ignore[arg-type]
                server_id="s",
                owner_id="o",
                created_by="o",
                label="l",
                kind="bearer",
                value=value,
            )
        )
    assert exc.value.code == "INVALID_INPUT"
