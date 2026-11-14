"""工具目录 DTO。

数据源是 `app/executors/catalog.py`，不是数据库。`tools` 表只承载运营侧
可变的 `enabled` 开关，目录的其余字段（名称/说明/参数 schema）以代码为准。
"""

from typing import Any

from pydantic import BaseModel, Field


class ToolOut(BaseModel):
    """单个工具。"""

    name: str
    display_name: str
    description: str
    kind: str = Field(description="builtin 进程内执行；sandbox 需隔离容器")
    enabled: bool = Field(description="运营侧开关；关闭后新运行不可选")
    param_schema: dict[str, Any] = Field(description="OpenAI function parameters 段")
    timeout_seconds: int


class ToolList(BaseModel):
    """工具目录响应。"""

    items: list[ToolOut]
    total: int
