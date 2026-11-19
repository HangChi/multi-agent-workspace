"""web_search 与内置工具注册的单测（015 覆盖率补齐）。

web_search 三个分支各自对应一种真实故障：
- 未配置 SEARCH_API_URL → 必须返回**可读的降级结果**（回填给 LLM，让它知道
  「没搜到」而不是编一个）；
- 搜索服务 5xx/超时 → 抛 SearchToolError（runner 记为该工具失败，而不是把
  异常当结果）；
- 正常响应 → 结果字段可能叫 results 也可能叫 items，且不保证是 list。
"""

import httpx
import pytest

from app.executors.http import (
    NoopExecutor,
    SearchToolError,
    WebSearchExecutor,
    builtin_tools,
    default_executors,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("SEARCH_API_URL", raising=False)
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)


async def test_web_search_without_query_returns_error():
    assert await WebSearchExecutor().execute({}) == {"error": "缺少 query 参数"}


async def test_web_search_unconfigured_returns_readable_degradation():
    """没配搜索服务时必须说清楚，而不是静默返回空列表。"""
    result = await WebSearchExecutor().execute({"query": "python 3.15"})

    assert "未配置" in result["error"]
    assert result["query"] == "python 3.15"


async def test_web_search_success_reads_results(monkeypatch):
    monkeypatch.setenv("SEARCH_API_URL", "https://search.test/api")
    monkeypatch.setenv("SEARCH_API_KEY", "k-1")

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": [{"title": "A"}]})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class _Patched(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Patched)

    result = await WebSearchExecutor().execute({"query": "q1"})

    assert result["results"] == [{"title": "A"}]
    assert seen["auth"] == "Bearer k-1"
    # 同时带 query 与 q：不同搜索服务的参数名不一致
    assert seen["params"] == {"query": "q1", "q": "q1"}


async def test_web_search_wraps_non_list_payload(monkeypatch):
    """items 不是 list 时包成单元素列表，避免下游 .map 崩。"""
    monkeypatch.setenv("SEARCH_API_URL", "https://search.test/api")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"items": {"title": "solo"}})
    )
    original = httpx.AsyncClient

    class _Patched(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Patched)

    result = await WebSearchExecutor().execute({"query": "q"})
    assert result["results"] == [{"title": "solo"}]


async def test_web_search_http_error_raises_tool_error(monkeypatch):
    monkeypatch.setenv("SEARCH_API_URL", "https://search.test/api")
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    original = httpx.AsyncClient

    class _Patched(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Patched)

    with pytest.raises(SearchToolError):
        await WebSearchExecutor().execute({"query": "q"})


def test_noop_executor_schema_and_loud_failure():
    """占位执行器必须显式报错：它的存在意义就是「别注册给 LLM」。"""
    executor = NoopExecutor("legacy", "旧占位工具")
    schema = executor.schema()

    assert schema["function"]["name"] == "legacy"
    assert schema["function"]["parameters"]["properties"]["input"]["type"] == "string"

    with pytest.raises(NotImplementedError):
        import asyncio

        asyncio.run(executor.execute({}))


def test_builtin_tools_and_default_registry():
    names = [tool.name for tool in builtin_tools()]
    assert names == ["web_search", "calculate", "read_url"]

    registry = default_executors()
    assert set(registry) == set(names)
