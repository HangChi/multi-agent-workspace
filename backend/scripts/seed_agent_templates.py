"""内置 Agent 模板种子脚本（005）。

用法：
    .venv/bin/python scripts/seed_agent_templates.py [--dry-run]

为什么内置模板走脚本而不是迁移（research R-02）：

1. `alembic/versions/` 下没有任何迁移导入 `app.*`（仓库既有约定），迁移要能在
   无应用上下文、无 `.env` 的环境里跑。
2. 内置模板的模型 MUST 跟随 `LLM_MODEL`。写死一个模型名会把「换端点后所有内置
    模板都指向不存在的模型」这个坑固化进 schema —— 2026-11-18 接新端点时已经
   踩过一次（`No available channel for model gpt-4o-mini`）。

幂等按 `(owner_id IS NULL, name)` 定位：已存在就跳过，**不更新**——
用户可能已经基于内置模板另存过自己的副本，而内置行本身是只读的；
真要改文案就改本文件再删旧行，脚本不负责覆盖。
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.executors.catalog import CATALOG
from app.models.agent_template import AgentTemplate
from app.models.tool import Tool
from app.schemas.agent_template import AgentTemplateCreate

# 内置模板的工具偏好。脚本会按「当前启用的目录项」过滤，
# 某个工具下线时对应模板只是少一个工具，不会整体作废（FR-008 的同一种哲学）。
_PREFERRED_TOOLS = {
    "通用问答助手": ["web_search", "read_url", "calculate"],
    "代码审查助手": ["run_python", "read_url", "calculate"],
    "数据摘要助手": ["web_search", "read_url", "run_python"],
    "资料检索助手": ["web_search", "read_url"],
}

_PROMPTS = {
    "通用问答助手": (
        "你是一个严谨的通用问答助手。\n"
        "回答时遵循：\n"
        "1. 先直接给出结论，再给依据；\n"
        "2. 不确定时明确说「不确定」，不要编造事实、数字或引用；\n"
        "3. 需要最新信息时优先使用搜索与网页读取工具，并标注信息来源；\n"
        "4. 涉及计算必须调用计算工具，不要口算。"
    ),
    "代码审查助手": (
        "你是一个资深代码审查助手。请从以下角度审查用户提交的代码：\n"
        "1. 正确性：边界条件、空值、并发与异常路径；\n"
        "2. 可读性：命名、结构、注释是否表达了意图；\n"
        "3. 安全隐患：注入、越权、敏感信息泄露、不安全的反序列化；\n"
        "4. 性能：明显的复杂度陷阱与重复计算。\n"
        "输出格式：按严重程度（必须修复 / 建议修复 / 可选优化）分组，"
        "每条指出文件与行号（若可得）、问题、原因与修改建议。"
        "能用工具验证的结论不要靠猜。"
    ),
    "数据摘要助手": (
        "你是一个数据摘要助手。用户会给你原始材料（网页、日志、表格或文本）。\n"
        "请输出：\n"
        "1. 一段不超过 150 字的结论摘要；\n"
        "2. 3-7 条关键事实，每条附出来源片段；\n"
        "3. 材料中相互矛盾或缺失之处的说明。\n"
        "严禁把材料里没有的信息补全；数字与日期必须与原文一致。"
    ),
    "资料检索助手": (
        "你是一个资料检索助手。请围绕用户的问题：\n"
        "1. 先用搜索找到候选来源，再读取最相关的 1-3 个页面正文；\n"
        "2. 交叉核对不同来源，标注一致与冲突之处；\n"
        "3. 最后给出结论，并在末尾列出引用来源的标题与链接。\n"
        "找不到可靠来源时直接说明，不要用模型记忆冒充检索结果。"
    ),
}

_DESCRIPTIONS = {
    "通用问答助手": "日常问答与解释，默认带搜索与计算能力",
    "代码审查助手": "按严重程度分组输出代码审查意见",
    "数据摘要助手": "把长材料压成结论 + 关键事实 + 矛盾说明",
    "资料检索助手": "先搜再读、交叉核对，结论附引用来源",
}


def builtin_payloads(
    *, model: str, enabled_tools: list[str]
) -> list[AgentTemplateCreate]:
    """构造 4 个内置模板的 payload（纯函数，可单测）。

    `model` 来自部署当前的 LLM 配置；`enabled_tools` 是当前启用的目录项，
    模板的工具集会与它取交集 —— 下线一个工具只会让模板少一个工具。
    """
    payloads: list[AgentTemplateCreate] = []
    for name, prompt in _PROMPTS.items():
        tools = [t for t in _PREFERRED_TOOLS[name] if t in enabled_tools]
        payloads.append(
            AgentTemplateCreate(
                name=name,
                description=_DESCRIPTIONS[name],
                system_prompt=prompt,
                model=model,
                temperature=0.7,
                max_steps=10,
                max_run_seconds=300,
                tools=tools,
                cost_limit_cents=500,
                egress_mode="default",
            )
        )
    return payloads


async def _enabled_tool_names(db) -> list[str]:
    rows = (await db.execute(select(Tool.name).where(Tool.enabled.is_(True)))).scalars()
    # 目录里实现但库里没有启用行的工具不注入（缺失行 = 拒绝，002 的约定）
    return [name for name in rows if name in CATALOG]


async def seed(*, dry_run: bool = False) -> int:
    """注入内置模板，返回新增条数。已存在则跳过（不更新）。"""
    async with SessionLocal() as db:
        enabled = await _enabled_tool_names(db)
        model = settings.llm_model
        payloads = builtin_payloads(model=model, enabled_tools=enabled)

        existing = set(
            (
                await db.execute(
                    select(AgentTemplate.name).where(AgentTemplate.owner_id.is_(None))
                )
            )
            .scalars()
            .all()
        )

        inserted = 0
        for payload in payloads:
            if payload.name in existing:
                print(f"跳过（已存在）: {payload.name}")
                continue
            if dry_run:
                print(
                    f"[dry-run] 将插入: {payload.name} | model={payload.model} | tools={payload.tools}"
                )
                inserted += 1
                continue
            db.add(
                AgentTemplate(
                    owner_id=None,
                    name=payload.name,
                    description=payload.description,
                    system_prompt=payload.system_prompt,
                    model=payload.model,
                    temperature=payload.temperature,
                    max_steps=payload.max_steps,
                    max_run_seconds=payload.max_run_seconds,
                    tools=payload.tools,
                    cost_limit_cents=payload.cost_limit_cents,
                    egress_mode=payload.egress_mode,
                )
            )
            inserted += 1
            print(
                f"已插入: {payload.name} | model={payload.model} | tools={payload.tools}"
            )

        if not dry_run and inserted:
            await db.commit()
        return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="注入内置 Agent 模板（幂等）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将插入哪些模板，不写数据库",
    )
    args = parser.parse_args()
    inserted = asyncio.run(seed(dry_run=args.dry_run))
    print(f"完成：新增 {inserted} 个内置模板（model={settings.llm_model}）")


if __name__ == "__main__":
    main()
