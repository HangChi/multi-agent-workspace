"""执行器基座（T066，US5）：ToolExecutor 协议与注册表（FR-014 工具调用）。

002 扩展：
- `DeniedToolResult`：越权调用的统一返回结构（对齐 contracts/api.md）。
  **不抛异常**——越权是预期内的正常分支，抛异常会打断 Run，而我们需要
  让模型知道「这个工具你没权限」并继续用别的工具完成。
- `ExecutorRegistry.from_names()`：按 `agents.tools` 授权列表构造注册表。
"""

import logging
from typing import Any, Protocol

from app.executors.catalog import CATALOG, ToolSpec

logger = logging.getLogger(__name__)


class ToolExecutor(Protocol):
    """工具执行器协议：name（工具名）+ schema（OpenAI 工具 JSON Schema）+ execute。"""

    name: str

    def schema(self) -> dict[str, Any]:
        """OpenAI 工具格式的 JSON Schema（供 LLM tool_call 声明）。"""
        ...

    async def execute(self, arguments: dict[str, Any]) -> Any:
        """执行工具调用，返回结果对象（可 JSON 序列化）。"""
        ...


class DeniedToolResult:
    """越权调用工具的返回结构（contracts/api.md）。

    形状固定为 `{"ok": false, "denied": true, "status": "denied", "message": ...}`，
    让模型能明确区分「工具没权限」与「工具执行失败」——前者换工具，后者重试。

    用静态方法而非 dataclass：这个结构要直接回填给 LLM 并落库，
    必须是纯 dict，不需要额外字段校验。
    """

    @staticmethod
    def build(tool_name: str, available: list[str] | None = None) -> dict[str, Any]:
        """构造越权结果。

        Args:
            tool_name: 被拒绝的工具名。
            available: 该 Agent 实际可用的工具名，用于告诉模型「你还能用什么」。
                不传则只说明未授权。

        Returns:
            可直接 JSON 序列化的 dict。
        """
        message = f"工具 {tool_name} 未被该 Agent 授权。"
        if available:
            message += f"可用工具：{', '.join(available)}。"
        return {
            "ok": False,
            "denied": True,
            "status": "denied",
            "message": message,
        }


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

    @classmethod
    def from_names(
        cls,
        names: list[str],
        catalog: dict[str, ToolSpec] | None = None,
        *,
        available: dict[str, ToolExecutor] | None = None,
    ) -> "ExecutorRegistry":
        """按授权工具名构造注册表。

        **缺工具的 name 静默跳过并记 warning**，不抛异常。原因：
        `agents.tools` 是运营数据，可能引用已下线/尚未实现的工具名
        （如管理员手工改库、或旧数据迁移残留）。此时应当「少给模型一个工具」
        而不是让整个 Run 起不来——用户看到的是「Agent 少了能力」，不是「系统挂了」。

        Args:
            names: 该 Agent 被授权的工具名（`agents.tools`）。
            catalog: 工具目录，默认用 `CATALOG`（测试可注入小目录）。
            available: 工具名 → 执行器实例。默认用内置工具集
                （延迟 import 避免 catalog ↔ executor 循环依赖）。

        Returns:
            只含成功匹配项的注册表。
        """
        from app.executors.http import builtin_tools

        spec_catalog = CATALOG if catalog is None else catalog
        if available is None:
            available = {ex.name: ex for ex in builtin_tools()}

        registry = cls()
        for name in names:
            executor = available.get(name)
            if executor is None:
                # 区分两种「没有」：目录里没这个名字 vs 目录有但执行器未注册
                reason = "不在工具目录中" if name not in spec_catalog else "无对应执行器实现"
                logger.warning("授权工具 %s 被跳过：%s", name, reason)
                continue
            registry.register(executor)
        return registry
