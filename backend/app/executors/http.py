"""HTTP 工具（T067，US5）：web_search 联网搜索 + Phase 1 内置工具集（FR-014）。

web_search：调用可配置的搜索 API 返回结果（默认走 Bing/Google 风格需配置，
此处实现为可插拔 HTTP 客户端；无配置时返回可读降级结果）。
其余 Phase 1 内置工具仅注册名称与 schema（无副作用），为后续 Phase 扩展预留。
"""

import os
from typing import Any

import httpx

from app.executors.base import ToolExecutor
from app.executors.calc import CalculateExecutor
from app.executors.read_url import ReadUrlExecutor


class SearchToolError(Exception):
    """搜索失败。"""


class WebSearchExecutor(ToolExecutor):
    """web_search：联网搜索并返回结构化结果。"""

    name = "web_search"

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "联网搜索公开信息，返回相关结果列表。用于需要最新事实、外部数据的场景。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词（中文/英文均可）"}
                    },
                    "required": ["query"],
                },
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query", "")
        if not query:
            return {"error": "缺少 query 参数"}

        endpoint = os.getenv("SEARCH_API_URL", "")
        api_key = os.getenv("SEARCH_API_KEY", "")
        if not endpoint:
            # 未配置搜索服务：返回可读降级（runner 会把该结果回填给 LLM）
            return {
                "error": "web_search 未配置（SEARCH_API_URL 未设置）",
                "query": query,
            }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    endpoint,
                    params={"query": query, "q": query},
                    headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise SearchToolError(f"搜索服务不可用: {exc}") from exc

        results = data.get("results") or data.get("items") or []
        return {
            "query": query,
            "results": results if isinstance(results, list) else [results],
        }


class NoopExecutor:
    """已废弃：占位工具会让 LLM 把「占位」当结果写进交付物。

    保留仅为兼容旧引用；builtin_tools 已改用真实实现（calculate / read_url）。
    新代码不要使用。
    """

    def __init__(self, name: str, description: str, params: dict[str, Any] | None = None) -> None:
        self.name = name
        self._description = description
        self._params = params or {
            "type": "object",
            "properties": {"input": {"type": "string", "description": "输入内容"}},
        }

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self._description,
                "parameters": self._params,
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(
            f"工具 {self.name} 无真实实现，不应注册给 LLM（会污染交付结果）"
        )


def builtin_tools() -> list[ToolExecutor]:
    """Phase 1 内置工具集：web_search + calculate + read_url（均为真实实现）。"""
    return [
        WebSearchExecutor(),
        CalculateExecutor(),
        ReadUrlExecutor(),
    ]


def default_executors() -> dict[str, ToolExecutor]:
    """默认执行器注册表（runner 未注入时使用）。"""
    return {executor.name: executor for executor in builtin_tools()}
