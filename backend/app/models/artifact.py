"""制品模型：运行/步骤产出的文件。"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Artifact(Base):
    """制品：MinIO 对象 + 元数据。"""

    __tablename__ = "artifacts"
    __table_args__ = (
        # 运行详情每次轮询都会按 run_id 取制品，制品下载按 id 解析单行；
        # 0006_artifact_run_step_index 之前这两列一直没有索引。
        Index("ix_artifacts_run", "run_id"),
        Index("ix_artifacts_step", "step_id"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    step_id: Mapped[str | None] = mapped_column(ForeignKey("steps.id"), nullable=True)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 内容指纹（002-sandboxed-tools）：沙箱产出的制品在服务端直传时流式计算，
    # 供去重与「上传后是否被篡改」核对。存量数据为 NULL（不回填）。
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    run = relationship("Run", back_populates="artifacts")
    step = relationship("Step", back_populates="artifacts")
