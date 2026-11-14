"""沙箱执行与出站访问 DTO。

对齐 contracts/api.md：
- `ExecutionSummary`：`GET /runs/{id}` 的 `executions` 数组元素（**不含** code/stdout/stderr，
  避免详情接口体积膨胀，R-04）
- `ExecutionDetail`：`GET /runs/{id}/executions/{eid}`
- `EgressAccessOut`：`GET /runs/{id}/executions/{eid}/egress`
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ExecutionSummary(BaseModel):
    """执行摘要（列表用）。

    刻意不含 `code`/`stdout`/`stderr`：一次执行的输出可达 MB 级，
    放进 Run 详情会让响应体积失控。
    """

    id: str
    step_seq: int = Field(description="对应 step 的 seq，前端据此关联到步骤时间线")
    language: str
    status: str = Field(description="ok/failed/timeout/resource/policy/infra/running")
    exit_code: int | None = None
    duration_ms: int
    stdout_truncated: bool
    artifacts: int = Field(default=0, description="该次执行产出的制品数")


class ExecutionArtifactBrief(BaseModel):
    """执行产出的制品摘要。"""

    id: str
    filename: str
    size_bytes: int


class ExecutionDetail(BaseModel):
    """执行详情。"""

    id: str
    run_id: str
    step_seq: int
    language: str
    status: str
    # 失败分类直接给出，不让前端靠 exit_code 猜（SC-008）
    error_category: str | None = None
    error_message: str | None = None
    exit_code: int | None = None
    code: str
    code_sha256: str
    stdout: str | None = None
    stderr: str | None = None
    stdout_truncated: bool
    stdout_full_bytes: int
    duration_ms: int
    # 读不到就是 None，不填 0——界面要能区分「没测到」与「真的是 0」
    cpu_ms: int | None = None
    mem_peak_bytes: int | None = None
    image: str
    limits: dict[str, Any]
    artifacts: list[ExecutionArtifactBrief] = Field(default_factory=list)
    created_at: datetime
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class EgressAccessOut(BaseModel):
    """一次出站访问记录。"""

    target_ip: str
    target_domain: str | None = Field(
        default=None, description="`ip-direct` 表示直连 IP 未反查到域名"
    )
    port: int
    action: str
    matched_rule_id: str | None = None
    bytes_out: int | None = None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class EgressAccessList(BaseModel):
    """出站访问记录列表。"""

    items: list[EgressAccessOut]
