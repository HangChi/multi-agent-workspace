"""ExecutorRegistry 的单测（015 覆盖率补齐）。

注册表是把「Agent 配置里的一串工具名」翻译成「真正能执行的执行器集合」的地方。
它的关键行为是**静默跳过不存在/未实现的工具**——所以必须能区分两种「没有」，
否则用户看到的是一条含糊的 warning，排查时不知道是名字打错了还是实现没注册。
"""

from app.executors.base import DeniedToolResult, ExecutorRegistry, ToolExecutor
from app.executors.http import WebSearchExecutor


class _Dummy(ToolExecutor):
    name = "dummy"

    def schema(self):
        return {"type": "function", "function": {"name": self.name}}

    async def execute(self, arguments):
        return {"ok": True}


def test_register_get_and_names_are_sorted():
    registry = ExecutorRegistry()
    registry.register(WebSearchExecutor())
    registry.register(_Dummy())

    assert isinstance(registry.get("dummy"), _Dummy)
    assert registry.get("nope") is None
    # names 排序：展示与测试都依赖稳定顺序
    assert registry.names == ["dummy", "web_search"]


def test_schemas_collects_every_executor():
    registry = ExecutorRegistry()
    registry.register(_Dummy())

    schemas = registry.schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "dummy"


def test_from_names_skips_unknown_and_logs_reason(caplog):
    """不在目录中 / 目录有但没实现，两种「没有」都要能区分。"""
    registry = ExecutorRegistry.from_names(["not-in-catalog"])

    assert registry.names == []
    assert any("not-in-catalog" in record.message for record in caplog.records)


def test_from_names_without_catalog_still_returns_registry():
    """不传 catalog 时用内置目录；未知名字不会抛异常（授权快照可能比代码旧）。"""
    registry = ExecutorRegistry.from_names(["totally-unknown-tool"])
    assert isinstance(registry, ExecutorRegistry)


def test_denied_tool_result_shape():
    """越权结果必须是纯 dict 且字段固定：模型靠它区分「没权限」与「执行失败」。"""
    result = DeniedToolResult.build("calculate", available=["read_url"])

    assert result["ok"] is False
    assert result["denied"] is True
    assert result["status"] == "denied"
    assert "calculate" in result["message"]
    assert "read_url" in result["message"]


def test_denied_tool_result_without_available_list():
    result = DeniedToolResult.build("calculate")
    assert result["denied"] is True
    assert "read_url" not in result["message"]


def test_protocol_default_bodies_return_none():
    """ToolExecutor 是 Protocol：子类不实现时桩体不返回数据，也不应抛异常。"""
    import asyncio

    class _Incomplete(ToolExecutor):
        name = "incomplete"

    executor = _Incomplete()
    assert executor.schema() is None
    assert asyncio.run(executor.execute({})) is None
