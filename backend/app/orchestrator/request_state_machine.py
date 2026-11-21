"""需求状态机（T010，007）：Request 状态迁移表 + 幂等迁移 + 不变量校验。

对齐 data-model.md §2：

```
created ──dispatch──▶ pending ──accept──▶ accepted ──assign(agent/group)──▶ running
   ▲                    │                  │                                │
   │                    │ reject           │ reject                         │
   └────────────────────┘                  └────────────────────────────────┘
```

与 Run 状态机（`state_machine.py`）的分工：Run 管「一次执行的生死」，
Request 管「一件需求的責任歸屬」。Request 跨多次 Run（重试/多 agent），
两者生命周期不同，刻意不合并——合并后「需求已完成但还有 retrying 的 Run」
无法表达（research R-09）。

本模块是纯函数：不依赖 DB，request 对象由调用方传入（ORM 或测试 dataclass）。
时间戳（dispatched_at/accepted_at）是业务字段，由 service 层维护，状态机只管
状态合法性——这样「转交不重置 accepted 语义」之类的策略变化不需要动机状态机。
"""

from enum import Enum
from typing import Any, Protocol


class RequestStatus(str, Enum):
    """需求状态机全部状态（对齐 request.status 枚举）。"""

    CREATED = "created"
    PENDING = "pending"
    ACCEPTED = "accepted"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# 终态：不可再迁移（与 Run 不同，Request 没有「重试离开终态」规则——
# 重试是 Run 级概念，需求终态后要再做只能新建需求）。
_TERMINAL = frozenset(
    {RequestStatus.COMPLETED, RequestStatus.FAILED, RequestStatus.CANCELLED}
)

# 对外导出的状态集合（R3）。
#
# 此前这些集合在 6 个模块里各写一份字面量，其中
# `dependency_service.ready_dependents` 的 skip 里混进了 `"timeout"`——
# 那是 **Run** 状态，`request_status` 枚举根本没有这个值，属于领域串味
# （PG 枚举上 NOT IN 带未定义值会直接报错，只是那处走的是 Python 层判定
# 才一直没炸）。集中到这里之后，新增需求状态只需改本文件，不会再漏。
#
# 注意区分：
# - `TERMINAL_STATUSES`（需求终态）≠ Run 的 `{completed, cancelled}`——
#   需求没有「重试离开终态」规则，`failed` 对它也是终态。
# - `ACTIVE_STATUSES`（需求未终结、看板该显示「还在跑」）只含 `running`；
#   `pending`/`accepted` 是分派链路的前置态，不是执行中。
TERMINAL_STATUSES: frozenset[str] = frozenset(s.value for s in _TERMINAL)
ACTIVE_STATUSES: frozenset[str] = frozenset({RequestStatus.RUNNING.value})
#: 非终态全集（终态的补集）。`schedule_service` 判「上次触发的需求还在跑吗」
#: 用的是它——只要不是终态就挡下一次触发，比列举 pending/accepted/... 可靠。
OPEN_STATUSES: frozenset[str] = (
    frozenset(s.value for s in RequestStatus) - TERMINAL_STATUSES
)

# 允许迁移表：from → {to, ...}
#
# created → running 是**既有链路的真实路径**（assign 端点直接把需求置 running，
# 中间不落 assigned；assigned 仅作为枚举/看板列保留兼容旧数据）。
# 007 新增的准入全部是「加边」：created→pending、pending→accepted、
# accepted→pending（转交）、pending|accepted→created（退回）、
# accepted→running（员工接收后拖给 agent）。
_TRANSITIONS: dict[RequestStatus, frozenset[RequestStatus]] = {
    RequestStatus.CREATED: frozenset(
        {RequestStatus.PENDING, RequestStatus.ASSIGNED, RequestStatus.RUNNING}
    ),
    RequestStatus.PENDING: frozenset(
        {
            RequestStatus.ACCEPTED,  # 接收
            RequestStatus.PENDING,  # 转交（同态；换接收人由 service 完成）
            RequestStatus.CREATED,  # 退回
        }
    ),
    RequestStatus.ACCEPTED: frozenset(
        {
            RequestStatus.PENDING,  # 转交给同事
            RequestStatus.ASSIGNED,  # 遗留中间态兼容
            RequestStatus.RUNNING,  # 拖给 agent（3b 新增准入）
            RequestStatus.CREATED,  # 退回
        }
    ),
    RequestStatus.ASSIGNED: frozenset({RequestStatus.RUNNING}),
    RequestStatus.RUNNING: frozenset(
        {
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.COMPLETED: frozenset(),
    RequestStatus.FAILED: frozenset(),
    RequestStatus.CANCELLED: frozenset(),
}

# 分派类操作（dispatch/accept/reject/reassign）的准入状态。
DISPATCHABLE_STATUSES = frozenset(
    {RequestStatus.CREATED, RequestStatus.PENDING, RequestStatus.ACCEPTED}
)

# 拖给 agent/组（既有 assign 端点）的准入状态。007 在 created/assigned 之外
# 加入 accepted——员工接收后的需求与自己的需求同权执行。
AGENT_ASSIGNABLE_STATUSES = frozenset(
    {RequestStatus.CREATED, RequestStatus.ASSIGNED, RequestStatus.ACCEPTED}
)


class TransitionError(ValueError):
    """非法需求状态迁移。service 层转换为 409 CONFLICT。"""


class RequestLike(Protocol):
    """迁移函数所需的最小接口（ORM Request 或测试 dataclass 均满足）。

    时间戳字段用 Any：ORM 侧是 ``datetime | None``、测试侧是 ``object``，
    协议属性不变式下写死任一种都会让另一边不匹配；这里只做存在性判断。
    """

    status: str
    assignee_id: str | None
    dispatched_at: Any
    accepted_at: Any


def is_terminal(status: str | RequestStatus) -> bool:
    """终态判定：completed/failed/cancelled。"""
    return RequestStatus(status) in _TERMINAL


def can_transition(current: str | RequestStatus, to: str | RequestStatus) -> bool:
    """只问不迁移：供服务层在写历史/发通知前预判（避免半写半抛）。"""
    cur = RequestStatus(current)
    target = RequestStatus(to)
    if cur == target:
        return True
    return target in _TRANSITIONS[cur]


def transition(request: RequestLike, to: RequestStatus) -> RequestStatus:
    """把需求从当前状态迁移到 to。

    - 非法迁移 → TransitionError（状态不变，调用方转 409）
    - 迁移到当前状态 → no-op（幂等；重复 accept/reject 由本层吸收）
    """
    current = RequestStatus(request.status)
    if current == to:
        return to
    if to not in _TRANSITIONS[current]:
        raise TransitionError(f"{current.value} 不可迁移到 {to.value}")
    request.status = to.value
    return to


def assert_dispatch_invariants(request: RequestLike) -> None:
    """分派不变量校验（测试与 service 收尾处双重锁定，data-model §2）。

    1. assignee_id 非空 ⇔ 状态 ∈ {pending, accepted}（created 不得残留）；
       running 及以后保留 assignee 快照（「这份活最终落在谁头上」可查）；
    2. accepted_at 非空 ⇔ 状态 = accepted。

    只抛 ValueError：它是「状态与指针不一致」这类数据损坏信号，
    与用户操作无关，不应以 409 呈现。
    """
    status = RequestStatus(request.status)
    has_assignee = request.assignee_id is not None
    in_dispatch = status in (RequestStatus.PENDING, RequestStatus.ACCEPTED)

    if in_dispatch and not has_assignee:
        raise ValueError(f"状态 {status.value} 要求 assignee_id 非空")
    if status == RequestStatus.CREATED and has_assignee:
        raise ValueError("状态 created 不得残留 assignee_id")
    if status == RequestStatus.ACCEPTED and request.accepted_at is None:
        raise ValueError("状态 accepted 要求 accepted_at 非空")
    if status != RequestStatus.ACCEPTED and request.accepted_at is not None:
        raise ValueError(f"状态 {status.value} 不得残留 accepted_at")
