"""外部凭据服务的头校验与生命周期分支（015 覆盖率补齐）。

`_validate_header` 是整个凭据链路的第一个闸门：它决定「Authorization 还是
X-API-Key」，一旦放行错误，后面所有调用都会把密钥贴错头、或者干脆把
Bearer 令牌当成 API Key 塞进 X-API-Key。这类错误不抛异常，只在服务端
静默 401，排查成本极高。

`create_credential` 的 kind/value 校验与 `rotate_credential` / `revoke_credential`
的状态机分支同样在这里锁死——它们都是「坏了会静默产生坏数据」的类型。
"""

import pytest

from app.api.errors import AppError
from app.models.external_credential import ExternalCredential
from app.services.external_credential_service import (
    _validate_header,
    create_credential,
    revoke_credential,
    rotate_credential,
)


class _FakeSession:
    """只提供 flush/add，验的是「抛不抛、状态怎么改」，不是 SQL。"""

    def __init__(self) -> None:
        self.added: list = []
        self.flushed = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1


# ---------- _validate_header ----------


@pytest.mark.parametrize(
    ("kind", "header_name", "expected"),
    [
        ("bearer", None, "authorization"),  # 缺省按 kind 推导
        ("api_key", None, "x-api-key"),
        ("bearer", "Authorization", "authorization"),  # 大小写/空格归一
        ("api_key", "X-API-Key", "x-api-key"),
    ],
)
def test_validate_header_accepts_defaults_and_normalizes(kind, header_name, expected):
    assert _validate_header(kind, header_name) == expected


@pytest.mark.parametrize("header_name", ["x-custom", "cookie", "x-forwarded-for"])
def test_validate_header_rejects_non_whitelisted_header(header_name):
    with pytest.raises(AppError, match="仅允许"):
        _validate_header("api_key", header_name)


def test_validate_header_forces_bearer_to_use_authorization():
    """Bearer 令牌只能进 Authorization 头——塞进 X-API-Key 会泄到错误的地方。"""
    with pytest.raises(AppError, match="Bearer"):
        _validate_header("bearer", "X-API-Key")


# ---------- create_credential ----------


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bearer", "api_key"])
async def test_create_credential_accepts_valid_kinds(kind):
    db = _FakeSession()
    cred = await create_credential(
        db,
        server_id="srv",
        owner_id="owner",
        created_by="owner",
        label="  prod token  ",
        kind=kind,
        value="secret-value",
    )
    assert isinstance(cred, ExternalCredential)
    assert cred.kind == kind
    assert cred.status == "active"
    assert cred.label == "prod token"  # 首尾空格被剥掉
    assert cred.ciphertext != "secret-value"  # 绝不存明文
    assert db.flushed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("bearer", ""),  # 空值
        ("bearer", "x" * 8193),  # 超长
        ("jwt", "x"),  # 非法 kind
    ],
)
async def test_create_credential_rejects_invalid_kind_or_value(kind, value):
    with pytest.raises(AppError, match="凭证类型或长度"):
        await create_credential(
            _FakeSession(),
            server_id="srv",
            owner_id="owner",
            created_by="owner",
            label="x",
            kind=kind,
            value=value,
        )


@pytest.mark.asyncio
async def test_create_credential_truncates_overlong_label():
    """label 截到 100 字符：它不是安全字段，但超长会撑爆列。"""
    cred = await create_credential(
        _FakeSession(),
        server_id="srv",
        owner_id="owner",
        created_by="owner",
        label="l" * 200,
        kind="api_key",
        value="v",
    )
    assert len(cred.label) == 100


# ---------- rotate_credential ----------


def _credential(**overrides):
    base = dict(
        server_id="srv",
        scope_owner_id="owner",
        label="prod",
        kind="api_key",
        header_name="x-api-key",
        ciphertext="enc",
        key_version="v1",
        status="active",
        created_by="owner",
    )
    base.update(overrides)
    return ExternalCredential(**base)


@pytest.mark.asyncio
async def test_rotate_credential_rejects_non_active_credential():
    with pytest.raises(AppError, match="只能轮换活动凭证"):
        await rotate_credential(
            _FakeSession(),
            old_credential=_credential(status="revoked"),
            created_by="owner",
            label="new",
            value="new-secret",
        )


@pytest.mark.asyncio
async def test_rotate_credential_rejects_kind_mismatch():
    """轮换不能改认证模式：bearer 换 api_key 会让服务端把密钥类型搞混。"""
    with pytest.raises(AppError, match="类型必须与当前认证模式一致"):
        await rotate_credential(
            _FakeSession(),
            old_credential=_credential(kind="api_key"),
            created_by="owner",
            label="new",
            value="new-secret",
            kind="bearer",
        )


@pytest.mark.asyncio
async def test_rotate_credential_marks_old_superseded_and_creates_new():
    db = _FakeSession()
    old = _credential()
    new = await rotate_credential(
        db,
        old_credential=old,
        created_by="owner",
        label="rotated",
        value="new-secret",
    )
    assert old.status == "superseded"
    assert old.rotated_at is not None
    assert new.status == "active"
    assert new.kind == old.kind  # 类型沿用旧的
    assert new.header_name == old.header_name  # 头也沿用
    assert new.ciphertext != "new-secret"


# ---------- revoke_credential ----------


@pytest.mark.asyncio
async def test_revoke_credential_marks_revoked():
    db = _FakeSession()
    cred = _credential(status="active")
    await revoke_credential(db, cred)
    assert cred.status == "revoked"
    assert cred.rotated_at is not None
    assert db.flushed == 1


@pytest.mark.asyncio
async def test_revoke_credential_is_idempotent():
    """重复撤销是 no-op：不能再翻转 rotated_at 造成审计歧义。"""
    db = _FakeSession()
    cred = _credential(status="revoked")
    await revoke_credential(db, cred)
    assert db.flushed == 0
    assert cred.rotated_at is None
