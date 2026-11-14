"""应用配置（pydantic-settings）。字段对齐 deploy/.env.example。"""

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 代码内置的默认值：仅允许在开发环境使用
_WEAK_DEFAULT_SECRETS = frozenset({"dev-only-change-me", "CHANGE-ME-ADMIN-CODE"})
_PRODUCTION_ENVS = frozenset({"production", "prod"})
_MIN_JWT_SECRET_LEN = 32
_MIN_INVITE_CODE_LEN = 12


class Settings(BaseSettings):
    """全局配置，从环境变量读取。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 基础
    app_name: str = "agent-workbench"
    app_env: str = "development"
    log_level: str = "info"

    # 数据库（默认对齐 deploy/docker-compose.yml 的本地开发配置）
    database_url: str = "postgresql+asyncpg://aw:aw-dev-password@localhost:5432/agent_workbench"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # JWT
    jwt_secret: str = "dev-only-change-me"
    jwt_access_ttl_minutes: int = 30
    jwt_refresh_ttl_days: int = 7
    jwt_algorithm: str = "HS256"
    # refresh 宽限期（秒）：多标签页并发刷新时不互相踢；0 = 严格一次性
    refresh_grace_seconds: int = 30

    # 邀请码
    invite_code: str = "CHANGE-ME-ADMIN-CODE"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "agent-workbench"
    minio_secure: bool = False

    # LLM（LiteLLM）
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = ""

    # CORS
    cors_origins: str = Field(default="http://localhost:5173")

    # 附件限制
    max_attachment_size_mb: int = 20
    allowed_attachment_types: str = (
        "text/plain,text/markdown,application/pdf,application/json,"
        "image/png,image/jpeg,image/gif,application/zip,application/x-tar,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # 配额
    monthly_quota_cents: int = 1_000_000  # 用户月配额（分）

    # 僵尸 Run 回收阈值（Beat 周期任务）
    # queued 超过该分钟数仍未被执行 → 判定 Worker 未消费（消息丢失/Worker 未起）
    stale_queued_minutes: int = 30
    # running 超过该分钟数仍无进展 → 判定 Worker 执行中被杀（默认 max_run_seconds 1800，留余量）
    stale_running_minutes: int = 90

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_attachment_type_list(self) -> list[str]:
        return [t.strip() for t in self.allowed_attachment_types.split(",") if t.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in _PRODUCTION_ENVS

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> "Settings":
        """生产环境拒绝弱默认密钥（T010「启动时校验必需项」）。

        忘了配 JWT_SECRET 时用默认值跑生产 = 任何人可伪造 token（完全绕过鉴权），
        因此这里直接启动失败，而不是降级运行。
        """
        if not self.is_production:
            return self

        problems: list[str] = []
        if self.jwt_secret in _WEAK_DEFAULT_SECRETS or len(self.jwt_secret) < _MIN_JWT_SECRET_LEN:
            problems.append(f"JWT_SECRET（需 ≥{_MIN_JWT_SECRET_LEN} 字符且非默认值）")
        if self.invite_code in _WEAK_DEFAULT_SECRETS or len(self.invite_code) < _MIN_INVITE_CODE_LEN:
            problems.append(f"INVITE_CODE（需 ≥{_MIN_INVITE_CODE_LEN} 字符且非默认值）")
        if problems:
            raise ValueError(
                f"APP_ENV={self.app_env} 下必须显式配置安全项：" + "、".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
