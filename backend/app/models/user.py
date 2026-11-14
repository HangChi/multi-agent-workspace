"""用户模型。"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """用户：注册账号，三角色 RBAC。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("admin", "member", "viewer", name="user_role"), nullable=False, default="member"
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "disabled", name="user_status"), nullable=False, default="active"
    )
    invite_code_id: Mapped[str | None] = mapped_column(ForeignKey("invite_codes.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # users ↔ invite_codes 有两条外键路径（users.invite_code_id 与 invite_codes.created_by），
    # 必须显式指定 foreign_keys 消歧
    invite_code = relationship(
        "InviteCode",
        back_populates="users",
        foreign_keys=[invite_code_id],
    )
    agents = relationship("Agent", back_populates="owner")
    groups = relationship("AgentGroup", back_populates="owner")
    requests = relationship("Request", back_populates="owner")
    runs = relationship("Run", back_populates="owner")
