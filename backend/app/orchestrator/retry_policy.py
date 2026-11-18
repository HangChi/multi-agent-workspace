"""重试策略：失败分类 + 指数退避（006 / T008）。

两个纯函数，不 import 任何 IO 模块——这是刻意的：重试决策发生在 Worker 的
异常处理路径上，那里任何额外 import 失败都会把「标失败」这条保底路径也带崩。

设计依据见 `specs/006-run-retry/research.md`：

- **R-05 未知异常按业务类**：瞬时故障的特征是明确的（连接失败/超时/网关 5xx），
  白名单能列全；而未知异常里最可能的是代码 bug，对 bug 重试 3 次只是 3 倍日志
  噪声与 3 倍可能已产生的部分成本。
- **R-02 full jitter**：多个 Run 往往因为同一个原因同时失败（网关重启、网络
  抖动）。固定间隔会让它们在同一个秒数一起重试，把刚恢复的网关再次打挂。
  AWS《Exponential Backoff and Jitter》的 full jitter：`random(0, 计算值)`。
"""

import random
from enum import Enum
from typing import Any


class FailureKind(str, Enum):
    """失败的两种性质。

    `TRANSIENT`：确知会自愈（网络抖动、网关重启、瞬时超时）——值得重试。
    `PERMANENT`：重试一次还是同样结果（配额/成本/步数/取消/策略拒绝/bug）
    ——重试只是浪费。
    """

    TRANSIENT = "transient"
    PERMANENT = "permanent"


# 瞬时类的连接层异常。OSError 覆盖大部分 socket 错误（ECONNRESET/ECONNREFUSED/
# EPIPE），ConnectionError 与 TimeoutError 是它的子类，列出来只为可读性。
_CONNECTION_ERRORS = (ConnectionError, TimeoutError, OSError)


def classify_failure(exc: BaseException | None) -> FailureKind:
    """判定一个异常是否值得自动重试。

    瞬时类白名单：
    1. 连接层异常（`ConnectionError` / `TimeoutError` / `OSError`）
    2. `LLMClientError` 且 `transient=True`（调用点明确知道是网关/超时问题）

    其余一律 `PERMANENT`，包括：配额/成本/步数/执行超时、用户取消、
    工具策略拒绝、鉴权与限流错误（换密钥或等额度，重试同一个请求不会好），
    以及所有未知异常。

    `exc is None` 是防御性分支：调用方误传时不崩，按业务类处理。
    """
    if exc is None:
        return FailureKind.PERMANENT

    if isinstance(exc, _CONNECTION_ERRORS):
        return FailureKind.TRANSIENT

    # LLMClientError 用 transient 标记区分「网关抖动」与「鉴权/限流」——
    # 两者在 LiteLLM 侧是同一个异常类，但重试价值完全相反。
    if getattr(exc, "transient", False) is True:
        return FailureKind.TRANSIENT

    return FailureKind.PERMANENT


def backoff_seconds(attempt: int, base_seconds: float, cap_seconds: float) -> float:
    """第 `attempt` 次失败后的重试等待时长（full jitter）。

    - 理论上界：`min(cap, base × 2^(attempt-1))`——attempt 从 1 开始，
      第一次重试等一个基准，第二次翻倍，以此类推。
    - 实际返回：`random.uniform(0, 理论上界)`，即 full jitter。
    - 上限截断：attempt 很大时不得无限增长。

    Args:
        attempt: 第几次尝试失败（从 1 开始；1 = 首次执行失败）
        base_seconds: 退避基准秒数，必须 > 0
        cap_seconds: 退避封顶秒数，必须 >= base_seconds

    Raises:
        ValueError: 参数非法时直接炸，不返回假值——配置错了还静默退避成 0
            会导致所有失败任务立刻重试，那比不重试更糟。
    """
    if attempt < 1:
        raise ValueError(f"attempt 必须从 1 开始，收到 {attempt}")
    if base_seconds <= 0:
        raise ValueError(f"base_seconds 必须 > 0，收到 {base_seconds}")
    if cap_seconds < base_seconds:
        raise ValueError(
            f"cap_seconds({cap_seconds}) 不能小于 base_seconds({base_seconds})"
        )

    ceiling = min(cap_seconds, base_seconds * (2 ** (attempt - 1)))
    return random.uniform(0.0, ceiling)


def is_retryable(exc: BaseException | None, attempt: int, max_attempts: int) -> bool:
    """是否应该安排下一次自动重试。

    三个条件同时满足：
    1. 失败是瞬时类（`classify_failure`）
    2. 还没到尝试上限（`attempt < max_attempts`；attempt 是「已经跑完第几次」）
    3. `max_attempts` 本身合法（<=1 等于关闭自动重试）

    拆成独立函数而不是内联进 `execute_run`：那里是异常处理路径，可读性优先，
    且这个判定需要被单测直接覆盖（集成测试只能覆盖组合后的结果）。
    """
    if max_attempts <= 1:
        return False
    if attempt >= max_attempts:
        return False
    return classify_failure(exc) is FailureKind.TRANSIENT


def retry_delay_for(
    exc: BaseException | None, attempt: int, base_seconds: float, cap_seconds: float
) -> float:
    """按失败性质选择等待时长：可重试按退避算，不可重试返回 0。

    单独包一层是为了让调用方（`execute_run` 的异常处理）只需要一个表达式，
    不需要在已经混乱的 except 块里再写一次 if/else。
    """
    if classify_failure(exc) is not FailureKind.TRANSIENT:
        return 0.0
    return backoff_seconds(attempt, base_seconds, cap_seconds)


def describe_kind(exc: Any) -> str:
    """给日志与审计用的人类可读分类名。"""
    return classify_failure(exc).value
