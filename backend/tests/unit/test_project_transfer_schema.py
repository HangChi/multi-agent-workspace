"""017 移交入参的 schema 契约（纯校验，不碰 DB）。"""

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.project import ProjectTransferIn


def test_default_keeps_old_owner_as_member() -> None:
    """默认保留访问：交接不等于立刻断联（research R-04）。"""
    payload = ProjectTransferIn(new_owner_id=uuid.uuid4())
    assert payload.keep_as_member is True


def test_accepts_explicit_false() -> None:
    payload = ProjectTransferIn(new_owner_id=uuid.uuid4(), keep_as_member=False)
    assert payload.keep_as_member is False


def test_new_owner_id_required() -> None:
    with pytest.raises(ValidationError):
        ProjectTransferIn()  # type: ignore[call-arg]


def test_new_owner_id_must_be_uuid() -> None:
    with pytest.raises(ValidationError):
        ProjectTransferIn(new_owner_id="not-a-uuid")  # type: ignore[arg-type]


def test_unknown_field_rejected_by_router_not_schema() -> None:
    """schema 默认忽略多余字段；多余字段由 FastAPI 正常忽略，不报 422。

    这条钉的是行为，不是理想：移交没有需要拒绝的扩展字段，
    静默忽略与 PATCH 的 exclude_unset 口径一致。
    """
    payload = ProjectTransferIn(new_owner_id=uuid.uuid4(), note="x")  # type: ignore[call-arg]
    assert not hasattr(payload, "note")
