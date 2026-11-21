"""邀请码申请（027）：member 提申请，admin 审批后发码。

022 明确没做这块，理由是「正确形态是配合组织编制的 HC 管理」。027 做的是一个
**更小的闭环**：申请 + 审批 + 自动发码，不复用 HC 概念、不做配额审批链。

为什么仍然值得做：现在加人只有一条路——在群里问管理员，管理员再去发码、贴回来。
三步都发生在系统外，谁批的、批了几个、为什么批，事后无从查起。
申请表把这三步留在系统内（审计表也有对应记录）。

刻意不做：
- 多级审批 / 审批人配置（一个公司一个 admin 足够）；
- 「申请被拒后自动通知申请人」之外的任何通知逻辑（当前只发站内通知）；
- 与 HC/编制挂钩（那是 HC 管理特性的事）。
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.user import new_uuid

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


class InviteRequest(Base):
    """一条邀请码申请。"""

    __tablename__ = "invite_requests"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    requester_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 给哪个部门招人（可空 = 不占部门预算）。批准时按它校验 022 的月度配额。
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="member", server_default="member"
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=STATUS_PENDING,
        server_default=STATUS_PENDING,
    )
    decided_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 批准时生成的码（逗号分隔的 id 列表太脆，直接存码值供申请人复制）
    issued_codes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_invite_requests_status",
        ),
        CheckConstraint("count BETWEEN 1 AND 20", name="ck_invite_requests_count"),
        CheckConstraint("role IN ('member', 'viewer')", name="ck_invite_requests_role"),
        # 待审批队列（admin 的主路径）与「我的申请」（member 的主路径）
        Index("idx_invite_requests_status", "status", "created_at"),
        Index("idx_invite_requests_requester", "requester_id", "created_at"),
    )
