"""应用配置（pydantic-settings）。字段对齐 deploy/.env.example。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_attachment_type_list(self) -> list[str]:
        return [t.strip() for t in self.allowed_attachment_types.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
