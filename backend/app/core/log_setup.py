"""structlog 配置：JSON/彩色输出 + contextvars 串联 + 敏感字段脱敏。

此前项目直接用 structlog 默认配置——没有时间戳统一格式、没有 request_id
串联（多请求并发时日志无法归因），也没有强制脱敏（靠人记得别打印 token）。
"""

import logging
import os
from typing import Any

import structlog

from app.core.config import settings

# 命中这些 key 的值一律替换（键名小写比较；子串匹配覆盖 access_token 等变体）
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "authorization",
    "secret",
    "api_key",
    "apikey",
    "credential",
    "private_key",
)

_REDACTED = "***"


def _is_sensitive(key: str) -> bool:
    # 归一化连字符：x-api-key 与 api_key 都要命中
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _redact(value: Any, depth: int = 0) -> Any:
    """递归脱敏（限制深度，避免异常结构导致递归过深）。"""
    if depth > 4:
        return value
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive(str(k)) else _redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value]
    return value


def redact_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor：对事件字典中的敏感字段脱敏。"""
    return _redact(event_dict)


def _level() -> int:
    return getattr(logging, settings.log_level.upper(), logging.INFO)


def configure_logging() -> None:
    """初始化 structlog（幂等，多次调用只生效一次）。"""
    if getattr(configure_logging, "_configured", False):
        return

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,  # request_id 等上下文自动带上
        structlog.processors.add_log_level,
        timestamper,
        redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # 生产输出 JSON（便于采集/检索）；本地开发用彩色可读格式
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.dev.ConsoleRenderer(colors=os.getenv("NO_COLOR") is None)
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(_level()),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 让第三方库（uvicorn/sqlalchemy/celery）的 stdlib 日志也走同一级别
    logging.basicConfig(level=_level(), format="%(message)s", force=False)

    configure_logging._configured = True  # type: ignore[attr-defined]
