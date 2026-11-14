"""工具目录端点（002 US2）。"""

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.executors.catalog import enabled_tools
from app.schemas.tool import ToolList, ToolOut

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=ToolList)
async def list_tools(_user: CurrentUser) -> ToolList:
    """返回当前代码版本中已实现、可供 Agent 授权的工具目录。"""
    items = [
        ToolOut(
            name=spec.name,
            display_name=spec.display_name,
            description=spec.description,
            kind=spec.kind,
            enabled=True,
            param_schema=spec.param_schema,
            timeout_seconds=spec.timeout_seconds,
        )
        for spec in enabled_tools()
    ]
    return ToolList(items=items, total=len(items))
