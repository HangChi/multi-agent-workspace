"""执行器基座（T066，US5）：ToolExecutor 协议与注册表（FR-014 工具调用）。"""

from typing import Any, Protocol


class ToolExecutor(Protocol):
    """工具执行器协议：name（工具名）+ schema（OpenAI 工具 JSON Schema）+ execute。"""

    name: str

    def schema(self) -> dict[str, Any]:
        """OpenAI 工具格式的 JSON Schema（供 LLM tool_call 声明）。"""
        ...

    async def execute(self, arguments: dict[str, Any]) -> Any:
        """执行工具调用，返回结果对象（可 JSON 序列化）。"""
        ...


class ExecutorRegistry:
    """工具名 → 执行器 的注册表（可替换，测试注入 FakeExecutor）。"""

    def __init__(self, executors: dict[str, ToolExecutor] | None = None) -> None:
        self._executors: dict[str, ToolExecutor] = dict(executors or {})

    def register(self, executor: ToolExecutor) -> None:
        self._executors[executor.name] = executor

    def get(self, name: str) -> ToolExecutor | None:
        return self._executors.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        """全部工具 schema（拼进 LLM 的 tools 参数）。"""
        return [executor.schema() for executor in self._executors.values()]

    @property
    def names(self) -> list[str]:
        return sorted(self._executors)
