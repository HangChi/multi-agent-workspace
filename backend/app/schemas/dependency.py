"""依赖边 schema（009）：出入参。

Out 带前置的标题与状态（`dependency_title` / `dependency_status`）——
详情弹层的边列表一次拿全，不必对每个前置再发一次 /requests/{id}。
反向视图（dependents）复用同一 schema，此时 `dependency_*` 字段语义
翻转为「被依赖方」的信息（request_id/title 侧）。
"""

import uuid

from pydantic import BaseModel


class DependencyEdgeCreate(BaseModel):
    # 前置需求 id（先做的那条）。可以是任何用户的需求——内部协作定位，
    # 「B 等隔壁团队的 A」是核心场景（research R-05）。
    dependency_id: uuid.UUID


class DependencyEdgeOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    request_id: str
    dependency_id: str
    # 对端需求的展示字段（direction 由端点决定语义，见模块 docstring）
    dependency_title: str | None = None
    dependency_status: str | None = None
    created_at: str


class DependencyEdgeList(BaseModel):
    items: list[DependencyEdgeOut]
    total: int
