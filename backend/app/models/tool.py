"""工具目录模型：工具的可运营面（启用开关、展示文案）。

字段值的**唯一事实源是 `app/executors/catalog.py`**（见 data-model.md）。
本表由 seed 迁移写入，只承载运营侧可变部分；定义仍来自 catalog，
可用性则取 catalog 与本表 enabled=true 行的交集（缺行也拒绝）。
禁用/删除时由迁移安装的触发器移除现存 Agent 授权，但不改历史 Run 快照。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Tool(Base):
    """工具目录项。

    `kind` 决定执行路径：`builtin` 走进程内执行器，`sandbox` 需隔离容器。
    两者在授权层面无差别（都受 `agents.tools` 控制），差别只在执行开销与隔离要求。
    """

    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(
        Enum("builtin", "sandbox", name="tool_kind"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    param_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    # 单次调用上限。sandbox 类会被执行配额覆盖（取两者较小值）。
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
