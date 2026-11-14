"""制品模型：运行/步骤产出的文件。"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Artifact(Base):
    """制品：MinIO 对象 + 元数据。"""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("steps.id"), nullable=True)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run = relationship("Run", back_populates="artifacts")
    step = relationship("Step", back_populates="artifacts")
