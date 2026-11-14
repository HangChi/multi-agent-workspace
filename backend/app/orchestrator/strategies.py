"""路由策略解析（T053）：direct / fan_out / router / pipeline 展开执行目标。

对齐 contracts/api.md 分配契约与 FR-007（组配置）：
- direct：展开 1 个执行目标（assignee 本身）
- fan_out：展开组内全部 active Agent（按 position 排序），生成 N 个子目标
- router：不展开子目标——父 Run 自己执行，运行时用 LLM 选一个成员（见 router.py）
- pipeline：展开组内全部 active Agent（按 position 排序），子 Run 顺序串联

策略是纯函数，不依赖 DB 会话；组对象由调用方预加载（含 members.agent）。
"""

from dataclasses import dataclass
from typing import Any


class NotImplementedStrategyError(ValueError):
    """策略尚未实现。"""


@dataclass(frozen=True)
class Target:
    """展开后的单个执行目标。"""

    assignee_type: str  # agent | group
    assignee_id: str


class StrategyResolver:
    """把「策略 + 分配目标」展开为具体执行目标列表。"""

    IMPLEMENTED = frozenset({"direct", "fan_out", "pipeline", "router"})

    @staticmethod
    def _group_members(group: Any) -> list[Target]:
        """组内可用成员（按 position 升序，排除归档）。"""
        members = sorted(group.members, key=lambda m: m.position)
        return [
            Target(assignee_type="agent", assignee_id=m.agent.id)
            for m in members
            if m.agent.status == "active"
        ]

    @classmethod
    def expand(
        cls,
        *,
        strategy: str,
        assignee_type: str,
        assignee_id: str,
        group: Any | None,
    ) -> list[Target]:
        """按策略展开执行目标列表。

        Args:
            strategy: direct | fan_out | pipeline | router
            assignee_type: agent | group
            assignee_id: 分配目标 id
            group: 当 assignee_type=group 时传入已预加载成员的组对象，否则 None

        Raises:
            NotImplementedStrategyError: 策略未实现
            ValueError: 未知策略 / 组策略缺少组对象 / 组内无可用成员
        """
        if strategy not in cls.IMPLEMENTED:
            raise ValueError(f"未知策略: {strategy}")

        if strategy == "direct":
            return [Target(assignee_type=assignee_type, assignee_id=assignee_id)]

        # 组类策略必须拖到组上
        if assignee_type != "group":
            raise ValueError(f"{strategy} 策略需分配给 Agent 组")
        if group is None:
            raise ValueError(f"{strategy} 策略需要组对象")

        if strategy == "router":
            # 父 Run 自己执行（运行时选成员），不需要子目标
            return [Target(assignee_type="group", assignee_id=assignee_id)]

        # fan_out / pipeline：展开全部可用成员
        targets = cls._group_members(group)
        if not targets:
            raise ValueError("组内无执行目标（成员为空或全部归档）")
        return targets
