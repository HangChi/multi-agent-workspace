"""Sentry 异常上报的初始化（可选能力）。

三条硬约束，避免「加了监控反而让服务起不来」：

1. **DSN 为空 = 功能不存在**：不 import sentry_sdk、不产生任何运行时开销。
2. **依赖可选**：sentry-sdk 不在主依赖里，只在 `[sentry]` extra 里。没装就被跳过。
3. **失败不阻断启动**：填了 DSN 但没装依赖，只记一条 warning —— 观测是增强
   能力，不是服务能跑起来的前提。这个判断是刻意的：如果 Sentry 挂掉能让
   API 起不来，那它就从「可观测性」变成了「单点故障」。

采样率口径：`traces_sample_rate` 是**性能追踪**的采样比例（0 = 只上报异常，
1 = 全量追踪）。生产建议 0.01~0.1 —— 追踪数据按事件计费，全量会烧钱。
"""

from __future__ import annotations

import structlog

from app import __version__
from app.core.config import settings

logger = structlog.get_logger(__name__)


def init_sentry() -> bool:
    """按配置初始化 Sentry；返回是否真的启用了。

    调用方不依赖返回值，返回它只是为了日志与测试能断言「到底启没启」。
    """
    if not settings.sentry_dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    except ImportError:
        # 常见误配：.env 里填了 DSN，但镜像是 no-dev 构建、没装 [sentry] extra
        logger.warning(
            "sentry_dsn_set_but_sdk_missing",
            hint="安装可选依赖后再启用：pip install -e '.[sentry]'",
        )
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=f"{settings.app_name}@{__version__}",
        # 夹紧到 [0, 1]：填成 5 或 -1 都会被 SDK 拒绝，不如在这里收口
        traces_sample_rate=max(0.0, min(1.0, settings.sentry_traces_sample_rate)),
        # 默认关：请求体里有需求正文、Agent 提示词，属于业务数据。
        # 要开就必须先确认 Sentry 项目的留存策略。
        send_default_pii=settings.sentry_send_default_pii,
        integrations=[
            FastApiIntegration(),
            SqlalchemyIntegration(),
            CeleryIntegration(),
        ],
    )
    logger.info("sentry_initialized", environment=settings.app_env)
    return True
