"""邀请码管理 DTO（015：管理员发放/查看/吊销）。"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


def invite_code_state(
    *,
    revoked_at: datetime | None,
    expires_at: datetime | None,
    used_count: int,
    max_uses: int,
    now: datetime | None = None,
) -> str:
    """active / exhausted / expired / revoked。

    由后端判定而不是让前端拼：四种「不可用」在界面上要分开显示（用尽 vs
    过期 vs 已吊销，处理动作不同），判定口径必须只有一处，否则前后端各写
    一份迟早不一致。
    """
    if revoked_at is not None:
        return "revoked"
    if used_count >= max_uses:
        return "exhausted"
    current = now or datetime.now(UTC)
    if expires_at is not None and expires_at <= current:
        return "expired"
    return "active"


class InviteCodeOut(BaseModel):
    """单个邀请码。

    码值直接在列表里返回——管理员本来就要把它发给同事，不像密码那样需要
    二次查看；泄露了可以吊销（这正是 revoked_at 存在的理由）。
    """

    id: str
    code: str
    role: str
    max_uses: int
    used_count: int
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    state: str = "active"


class InviteCodeList(BaseModel):
    """邀请码列表。码量属于管理员视图，量级为个位到几十，不分页。"""

    items: list[InviteCodeOut]
    total: int


class InviteCodeCreate(BaseModel):
    """发放邀请码。

    `max_uses` 上限 1000 挡住「填个 999999」造成的无限期通行证；
    `expires_in_days` 用相对天数而不是绝对时间：管理员不该被要求算 UTC。
    """

    role: Literal["member", "viewer"] = "member"
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class InviteCodeBatchCreate(InviteCodeCreate):
    """批量发码：参数同单张，另加数量。"""

    count: int = Field(default=3, ge=1, le=20)
