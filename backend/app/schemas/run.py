"""运行 schema（T070）：RunOut（详情含 steps/artifacts 摘要）。

对齐 contracts/api.md 运行部分（FR-016~019/FR-021）：
- RunOut: id/status/current_step/cost_cents/result/error/started_at/finished_at
  + steps（StepOut 摘要：seq/kind/status/tool_name/cost_cents）+ artifacts 摘要
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.sandbox import ExecutionSummary


class StepOut(BaseModel):
    """步骤摘要（详情页展示用）。"""

    id: str
    seq: int
    kind: str
    status: str
    tool_name: str | None = None
    duration_ms: int = 0
    cost_cents: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class ArtifactOut(BaseModel):
    """制品摘要（下载入口）。"""

    id: str
    filename: str
    size_bytes: int
    mime_type: str | None = None
    sha256: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ExternalToolInvocationOut(BaseModel):
    id: str
    step_id: str | None = None
    server_name: str
    tool_name: str
    stable_name: str
    version: str
    status: str
    duration_ms: int = 0
    error_category: str | None = None
    result_truncated: bool = False
    created_at: datetime


class RunOut(BaseModel):
    """运行详情。"""

    id: str
    request_id: str
    owner_id: str
    assignee_type: str
    assignee_id: str
    strategy: str
    status: str
    current_step: int = 0
    tool_grants_snapshot: list[str] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    cost_cents: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    steps: list[StepOut] = []
    artifacts: list[ArtifactOut] = []
    executions: list[ExecutionSummary] = []
    external_tool_invocations: list[ExternalToolInvocationOut] = []

    model_config = {"from_attributes": True}
