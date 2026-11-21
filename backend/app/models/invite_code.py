"""邀请码模型。"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.user import new_uuid


class InviteCode(Base):
    """邀请码：管理员生成，成员凭码注册。"""

    __tablename__ = "invite_codes"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=new_uuid
    )
    code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(
        Enum("member", "viewer", name="invite_role"), nullable=False, default="member"
    )
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 015：管理员吊销（泄露/发错人）。为空表示有效；不删行是因为
    # users.invite_code_id 引用它，删了会断掉注册来源的审计链。
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 022：这张码是给哪个部门招人用的（null = 未归属部门）。
    # 用于部门配额校验与「这个部门今年招了几个人」的统计。
    department_id: Mapped[str | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    users = relationship(
        "User",
        back_populates="invite_code",
        foreign_keys="User.invite_code_id",
    )

    __table_args__ = (Index("ix_invite_codes_department_id", "department_id"),)
