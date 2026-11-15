"""工具目录端点（002 US2）。"""

from fastapi import APIRouter

from app.api.deps import DB, CurrentUser
from app.schemas.tool import ToolList, ToolOut
from app.services.tool_service import enabled_tool_specs

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=ToolList)
async def list_tools(_user: CurrentUser, db: DB) -> ToolList:
    """返回已实现且运营侧启用、可供 Agent 授权的工具目录。"""
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
        for spec in await enabled_tool_specs(db)
    ]
    return ToolList(items=items, total=len(items))
