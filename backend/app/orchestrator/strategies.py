"""路由策略解析（T053）：direct / fan_out 展开执行目标列表。

对齐 contracts/api.md 分配契约与 FR-007（组配置）：
- direct：展开 1 个执行目标（assignee 本身）
- fan_out：展开组内全部 active Agent（按 position 排序），生成 N 个子目标
- pipeline / router：Phase 1 保留入口，显式抛「未实现」错误（避免静默错误行为）

策略是纯函数，不依赖 DB 会话；组对象由调用方预加载（含 members.agent）。
"""

from dataclasses import dataclass
from typing import Any


class NotImplementedStrategyError(ValueError):
    """策略尚未实现（Phase 1 保留入口）。"""


@dataclass(frozen=True)
class Target:
    """展开后的单个执行目标。"""

    assignee_type: str  # agent | group
    assignee_id: str


class StrategyResolver:
    """把「策略 + 分配目标」展开为具体执行目标列表。"""

    IMPLEMENTED = frozenset({"direct", "fan_out"})

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
            NotImplementedStrategyError: pipeline/router 未实现
            ValueError: 未知策略 / fan_out 无执行目标
        """
        if strategy not in cls.IMPLEMENTED:
            if strategy in ("pipeline", "router"):
                raise NotImplementedStrategyError(f"策略 {strategy} 未实现（Phase 1 保留入口）")
            raise ValueError(f"未知策略: {strategy}")

        if strategy == "direct":
            # direct：目标本身就是一个执行单元
            return [Target(assignee_type=assignee_type, assignee_id=assignee_id)]

        # fan_out：展开组内全部 active Agent（按 position 升序）
        if group is None:
            raise ValueError("fan_out 策略需要组对象")
        members = sorted(group.members, key=lambda m: m.position)
        targets = [
            Target(assignee_type="agent", assignee_id=m.agent.id)
            for m in members
            if m.agent.status == "active"
        ]
        if not targets:
            raise ValueError("组内无执行目标（成员为空或全部归档）")
        return targets
