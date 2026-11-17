"""工具目录：工具定义的单一片实源（single source of truth）。

**为什么需要这个文件**：工具信息此前散落在三处——执行器的 `schema()`、
`agents.tools` 里的名字、界面上的中文标签。三处各自演进必然漂移
（改了执行器的参数名，界面还在提示旧参数；数据库 seed 与代码不一致）。

这里把「工具是什么」收敛为一份声明，其余全部由它派生：
- `ExecutorRegistry.from_names()` 按名字取执行器
- `tools` 表的 seed 数据来自这里
- `/api/v1/tools` 的返回来自这里

**注意**：本文件不 import 任何执行器实现（避免 catalog ↔ executor 循环依赖）。
`param_schema` 是数据，不是从执行器反射出来的；改执行器 schema 时必须同步改这里，
`tests/unit/test_catalog.py` 会反向校验两者一致，防止漂移。
"""

from dataclasses import dataclass
from typing import Any, Literal

ToolKind = Literal["builtin", "sandbox"]


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的定义。

    Attributes:
        name: 工具名（LLM tool_call 里的 function name，也是 `agents.tools` 的元素）。
        display_name: 界面展示名（中文）。
        description: 给模型看的说明，与执行器 schema 的 description 一致。
        kind: `builtin` 进程内执行；`sandbox` 需隔离容器。
        param_schema: OpenAI function `parameters` 段（不含外层 type/function）。
        timeout_seconds: 单次调用上限；sandbox 类会被执行配额覆盖（取较小值）。
    """

    name: str
    display_name: str
    description: str
    kind: ToolKind
    param_schema: dict[str, Any]
    timeout_seconds: int


def _openai_schema(spec: ToolSpec) -> dict[str, Any]:
    """拼成 OpenAI tools 参数格式（执行器 `schema()` 返回的形状）。"""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.param_schema,
        },
    }


# 5 个工具。顺序即界面展示顺序：先检索类，再计算，最后代码执行。
CATALOG: dict[str, ToolSpec] = {
    "web_search": ToolSpec(
        name="web_search",
        display_name="联网搜索",
        description="联网搜索公开信息，返回相关结果列表。用于需要最新事实、外部数据的场景。",
        kind="builtin",
        param_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（中文/英文均可）",
                }
            },
            "required": ["query"],
        },
        timeout_seconds=30,
    ),
    "read_url": ToolSpec(
        name="read_url",
        display_name="读取网页",
        description=(
            "读取指定网页的正文内容（纯文本）。用于需要原文细节、"
            "而搜索摘要不够的场景。仅支持公网 http/https 地址。"
        ),
        kind="builtin",
        param_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "完整网址，如 https://example.com/post",
                }
            },
            "required": ["url"],
        },
        timeout_seconds=30,
    ),
    "calculate": ToolSpec(
        name="calculate",
        display_name="数值计算",
        description=(
            "计算数学表达式，支持 + - * / // % ** 与括号，"
            "以及 abs/round/min/max/sqrt/log/exp/floor/ceil/sin/cos/tan、pi、e。"
            "需要精确数值结果时使用，不要心算。"
        ),
        kind="builtin",
        param_schema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "待计算的表达式，如 (1250*0.8+300)/3",
                }
            },
            "required": ["expression"],
        },
        timeout_seconds=10,
    ),
    "run_python": ToolSpec(
        name="run_python",
        display_name="运行 Python",
        description=(
            "在隔离环境中运行 Python 代码并返回输出。适合数据处理、绘图、"
            "需要真实计算的场景。无网络限制时可直接访问公网。"
        ),
        kind="sandbox",
        param_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 代码"},
                "timeout_s": {
                    "type": "integer",
                    "description": "执行超时（秒），不填用默认值；超过上限会被夹取",
                },
            },
            "required": ["code"],
        },
        # 与 SBX_TIMEOUT_DEFAULT 对齐；实际取值由执行器夹取到 [1, SBX_TIMEOUT_MAX]
        timeout_seconds=120,
    ),
    "run_shell": ToolSpec(
        name="run_shell",
        display_name="运行命令",
        description=(
            "在隔离环境中运行 shell 命令并返回输出。适合文件操作、"
            "调用命令行工具的场景。"
        ),
        kind="sandbox",
        param_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout_s": {
                    "type": "integer",
                    "description": "执行超时（秒），不填用默认值；超过上限会被夹取",
                },
            },
            "required": ["command"],
        },
        timeout_seconds=120,
    ),
}

# 迁移映射：001 时代的旧工具名 → 002 的规范名。
# `http` 直接移除（无对应实现，且语义与 read_url 重叠）。
LEGACY_TOOL_NAMES: dict[str, str | None] = {
    "web_fetch": "read_url",
    "shell": "run_shell",
    "code_interpreter": "run_python",
    "http": None,
}


def get_tool(name: str) -> ToolSpec | None:
    """按名取工具定义；不存在返回 None（调用方决定是 warning 还是报错）。"""
    return CATALOG.get(name)


def enabled_tools() -> list[ToolSpec]:
    """全部工具定义（按 CATALOG 声明顺序）。

    注意：这里的「启用」指代码层面已实现。运营侧的开关在 `tools.enabled` 列，
    两者取交集才是用户可见目录——本函数不查库（catalog 保持纯数据，便于单测）。
    """
    return list(CATALOG.values())


def to_openai_schema(name: str) -> dict[str, Any] | None:
    """取某工具的 OpenAI schema；不存在返回 None。"""
    spec = CATALOG.get(name)
    return _openai_schema(spec) if spec else None
