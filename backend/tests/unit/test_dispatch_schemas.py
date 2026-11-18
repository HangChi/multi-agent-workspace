"""007 分派 schema 单测（T013）：枚举扩展 + RejectIn 校验 + note 约束。

锁的是 pydantic 层的边界：422 与 409 的分层——body 形状问题在 schema 层
拦（422），状态/权限问题在 service 层拦（409/404）。混层会让前端拿到
语义错误的错误码。
"""

import pytest
from pydantic import ValidationError

from app.schemas.request import (
    AssigneeType,
    AssignRequest,
    DispatchActionOut,
    RejectIn,
    RequestStatus,
)

# ---------- 枚举扩展（007） ----------


def test_request_status_has_pending_and_accepted():
    assert RequestStatus.pending.value == "pending"
    assert RequestStatus.accepted.value == "accepted"
    assert {s.value for s in RequestStatus} >= {
        "created",
        "pending",
        "accepted",
        "assigned",
        "running",
        "completed",
        "failed",
        "cancelled",
    }


def test_assignee_type_has_user():
    """人作为落点（拖到员工卡 = 转交责任）。"""
    assert AssigneeType.user.value == "user"
    assert {t.value for t in AssigneeType} == {"agent", "group", "user"}


# ---------- AssignRequest：note ----------


def test_assign_request_note_optional_and_bounded():
    body = AssignRequest(assignee_type="user", assignee_id="u1")
    assert body.note is None
    body = AssignRequest(assignee_type="user", assignee_id="u1", note="x" * 2000)
    assert len(body.note) == 2000
    with pytest.raises(ValidationError):
        AssignRequest(assignee_type="user", assignee_id="u1", note="x" * 2001)


def test_assign_request_agent_ignores_note_semantically():
    """agent 分支note 可传但服务端忽略（契约 §3）：schema 不替业务做分支。"""
    body = AssignRequest(assignee_type="agent", assignee_id="a1", note="忽略我")
    assert body.note == "忽略我"


def test_assign_request_strategy_pattern_unchanged():
    """strategy 白名单不变（007 不加新策略）。"""
    with pytest.raises(ValidationError):
        AssignRequest(assignee_type="group", assignee_id="g1", strategy="dag")


# ---------- RejectIn：原因必填 ----------


def test_reject_reason_required():
    with pytest.raises(ValidationError):
        RejectIn(reason=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RejectIn()  # type: ignore[call-arg]


def test_reject_reason_not_blank():
    with pytest.raises(ValidationError):
        RejectIn(reason="   ")
    assert RejectIn(reason=" 人手不够 ").reason == "人手不够"


def test_reject_reason_bounded():
    assert len(RejectIn(reason="x" * 2000).reason) == 2000
    with pytest.raises(ValidationError):
        RejectIn(reason="x" * 2001)


# ---------- DispatchActionOut ----------


def test_dispatch_action_out_shape():
    out = DispatchActionOut(request_id="r1", status="accepted")
    assert out.request_id == "r1"
    assert out.status == "accepted"
