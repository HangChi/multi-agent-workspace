"""资源错误文案的单一来源（R2，docs/code-audit-2026-11-21.md）。

## 为什么需要这个文件

两类问题，都不是风格洁癖：

1. **契约要求文案逐字一致**。016 契约 §2：非相关人的 404 message 必须与
   「资源确实不存在」**逐字相同**，否则 message 自身泄露存在性。
   同一句话写在 13 个地方，任何一处被顺手改成「找不到项目」就是契约破裂，
   而**没有任何测试会变红**。

2. **有人拿文案当控制流**。`schedule_service._translate_assign_failure`
   靠 `"归档" in message` / `"不存在" in message` 把分配失败归类成
   契约 §2 的白名单措辞。改一个字，归类就**静默**落到兜底的
   「目标不可用」——编译器和类型检查都发现不了，测试也只会在
   恰好覆盖该分支时红。

第 2 条用常量只解决一半（至少改文案要经过同一个文件），真正的解法是
`raise_for_missing` / `raise_archived` 这类**带标记的构造函数**：调用方
标记好语义，归类处读标记而不是读字符串。见下面的 `FailureKind`。
"""

from __future__ import annotations

from enum import Enum
from typing import NoReturn

from app.api.errors import AppError

#: 资源名 → 404 文案。
#:
#: 键是**领域实体**而不是端点名：`request` 同时服务
#: GET /requests/{id} 与 POST /requests/{id}/assign 等多个端点，
#: 它们的 404 文案必须完全一样（否则可以靠文案差异反推 id 是否存在）。
ERROR_MESSAGES: dict[str, str] = {
    "request": "需求不存在",
    "agent": "Agent 不存在",
    "group": "组不存在",
    "project": "项目不存在",
    "schedule": "调度不存在",
    "user": "用户不存在",
    "run": "运行不存在",
    "sandbox_execution": "沙箱执行不存在",
    "artifact": "制品不存在",
    "department": "部门不存在",
    "external_server": "外部工具服务器不存在",
    "external_credential": "外部工具凭证不存在",
    "member": "成员关系不存在",
    "invite": "邀请不存在",
}

#: 「前置」这类带修饰的 404——与主实体文案刻意区分。
#:
#: 加边时"前置需求不存在"要说清是哪一头缺失，否则用户不知道该去修哪条边。
#: 但它不能与 `ERROR_MESSAGES["request"]` 混用：依赖端点的目标侧 404
#: 必须与「真的不存在」区分开，否则「我加的边指向一个不存在的需求」
#: 与「我没有权限看这个需求」会被混成一件事。
DEPENDENCY_TARGET_MESSAGE = "前置需求不存在"


class FailureKind(str, Enum):
    """失败归类的**结构化标记**（替代字符串匹配）。

    `schedule_service._translate_assign_failure` 原先读 message 里的
    「归档」「不存在」「配额」来归类。这里把语义提到异常对象上：
    产生异常的地方标记好，消费的地方读标记——改文案不再影响归类。
    """

    ARCHIVED = "archived"  # 目标已归档（可恢复）
    MISSING = "missing"  # 目标已不存在（不可恢复）
    QUOTA = "quota"  # 配额/频率限制
    OTHER = "other"  # 兜底


def raise_for_missing(resource: str, *, kind: FailureKind | None = None) -> NoReturn:
    """抛标准 404：文案取自 `ERROR_MESSAGES`，可附带结构化失败原因。

    Args:
        resource: `ERROR_MESSAGES` 的键。
        kind: 供上层归类用的标记。不传时按 `MISSING` 处理——
            「资源不存在」本身就是 MISSING，绝大多数调用点不用显式写。

    Returns:
        永不返回（`NoReturn`）。**标注它是类型检查的一部分**：调用方通常写成
        `if x is None: raise_for_missing("project")` 然后直接 `x.owner_id`，
        没有 NoReturn 时 mypy 会认为 x 仍可能是 None 并报 union-attr。

    Raises:
        KeyError: resource 不在表里。**刻意不提供默认文案**：拼错的资源名
            应该立刻炸，而不是静默生成「资源不存在」——那正是文案漂移的起点。
    """
    message = ERROR_MESSAGES[resource]
    exc = AppError("NOT_FOUND", message)
    mark_failure(exc, kind or FailureKind.MISSING)
    raise exc


def mark_failure(exc: AppError, kind: FailureKind) -> AppError:
    """在异常对象上打归类标记（不改其 code / message / status）。"""
    exc.failure_kind = kind  # type: ignore[attr-defined]
    return exc


def failure_kind_of(exc: AppError) -> FailureKind:
    """读归类标记：未标记的按 OTHER 处理（与旧实现兜底一致）。"""
    kind = getattr(exc, "failure_kind", None)
    return kind if isinstance(kind, FailureKind) else FailureKind.OTHER
