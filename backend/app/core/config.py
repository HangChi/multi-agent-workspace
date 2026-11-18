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

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 基础
    app_name: str = "agent-workbench"
    app_env: str = "development"
    log_level: str = "info"

    # 数据库（默认对齐 deploy/docker-compose.yml 的本地开发配置）
    database_url: str = (
        "postgresql+asyncpg://aw:aw-dev-password@localhost:5432/agent_workbench"
    )

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
    # refresh token 的 HttpOnly cookie 名。前端不再持久化 refresh token，
    # 只有这个 cookie 持有它 —— XSS 读不到，能显著降低「长期账号接管」的风险。
    refresh_cookie_name: str = "aw_refresh"
    # cookie 作用域：只在认证端点发送，其他接口不带（减少暴露面）
    refresh_cookie_path: str = "/api/v1/auth"

    # 邀请码
    invite_code: str = "CHANGE-ME-ADMIN-CODE"

    # 登录防爆破（见 core/login_limiter.py）。
    # 只对失败计数，登录成功清零；Redis 不可用时放行。
    # 账号维度阈值故意设小（定向爆破单账号），IP 维度设大（撞库要换很多账号才触发）。
    login_max_fails_per_account: int = 5
    login_max_fails_per_ip: int = 30
    # 计数窗口 = 锁定时长（分钟），两个维度共用
    login_lockout_minutes: int = 15

    # MinIO
    minio_endpoint: str = "localhost:9000"
    # 浏览器可访问的地址，用于生成上传/下载的直连 URL。
    # ⚠️ 不能复用 minio_endpoint：那是**容器内**服务名（如 minio:9000），
    # 浏览器解析不了、多数组网也访问不到；只有 API/Worker 在 compose 网络内能用。
    # 留空时退化用 minio_endpoint（仅适用于 endpoint 本身就可被浏览器访问的
    # 单机部署，例如 localhost）。
    minio_public_endpoint: str = ""
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "agent-workbench"
    minio_secure: bool = False

    # LLM（LiteLLM）
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = ""
    # 单次 chat 调用的超时（秒）。必须显式设置：LiteLLM/OpenAI 默认等待可达
    # 10 分钟，而 runner 的超时检查只发生在「每轮循环之前」——一次挂死的调用
    # 会占满 Worker 槽位（默认并发 2），两个请求就能让 Worker 停摆。
    llm_timeout_seconds: float = 90.0

    # ---- 006 运行自动重试 ----
    # 总尝试次数（含首次）。设为 1 等于关闭自动重试。
    run_max_attempts: int = 3
    # 退避基准（秒）：第一次重试的理论等待。full jitter 后实际值在 [0, 基准] 内。
    run_retry_base_seconds: float = 2.0
    # 退避封顶（秒）：attempt 很大时不得无限增长。
    run_retry_cap_seconds: float = 60.0
    # 上下文预算（字符，≈4 字符/token）。超过后 runner 会从最早的轮次开始省略
    # 工具输出，只保留 system + 原始需求 + 最近若干轮，避免多步执行时 prompt
    # 无限膨胀（每一步都要为前面所有工具输出重复付费）。0 = 不裁剪。
    llm_max_context_chars: int = 48_000

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

    # ===== 代码沙箱（002-sandboxed-tools，research R-01/R-06/R-08）=====
    # 镜像：只允许这里列出的 tag 被起容器（防止配置被改成任意镜像 = 绕过隔离）。
    # 逗号分隔；`SBX_IMAGE` 必须属于该集合，否则启动失败。
    sbx_image: str = "aw-sandbox-python:3.12"
    sbx_allowed_images: str = "aw-sandbox-python:3.12"
    # 单次执行时长上限（秒）：默认值与硬上限。请求超过硬上限直接拒绝，
    # 不允许 LLM 传个 86400 把 Worker 占住。
    sbx_timeout_default: int = 120
    sbx_timeout_max: int = 600
    # 资源配额：CPU 核数、内存、磁盘（/tmp 与 workspace 合计的 tmpfs/写盘上限）、进程数
    sbx_cpu: float = 1.0
    sbx_mem_mb: int = 512
    sbx_disk_mb: int = 256
    sbx_pids: int = 128
    # 单机同时运行的沙箱数（Redis 信号量 sbx:sem:*）；超出的排队而非拒绝
    sbx_max_concurrency: int = 4
    # 输出与代码体积上限：stdout/stderr 各保留尾部该字节数；脚本源码超过则拒绝执行
    sbx_stdout_max_bytes: int = 1_048_576
    sbx_code_max_bytes: int = 262_144
    # 制品回收上限：output/ 目录下的文件数量、单文件、总量
    sbx_artifact_max_files: int = 50
    sbx_artifact_max_file_mb: int = 20
    sbx_artifact_max_total_mb: int = 50
    # 每次执行独立 bridge 网络的**名字前缀**（research R-02）。
    # 不能是「一个预建网络」：iptables 规则按网络接口名（`-i aw-sbx-<exec_id>`）
    # 打标签，共用网络会让并发执行互相删规则、也无法按执行粒度做限制模式。
    sbx_network_prefix: str = "aw-sbx"
    # dns-proxy 的容器名：Worker 用 docker API 找到它，并把它接入每次执行的独立网络，
    # 再把沙箱容器的 DNS 显式指向它（实测：不显式指 `dns=` 时会走 Docker 内嵌 DNS
    # 127.0.0.11，代理侧看不到查询，SC-009 的域名记录就断了）。
    egress_dns_proxy: str = "aw-dns-proxy"

    # ===== 出站策略（spec 修订版：默认放行 + 全量记录 + 全局黑名单）=====
    # 内置黑名单（逗号分隔 CIDR）：内网段、云元数据地址、回环。这层不可被成员绕过。
    # 169.254.169.254 是云厂商元数据服务，能取到机器临时凭证，漏掉等于交出账号一部分。
    egress_builtin_blacklist: str = (
        "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16,127.0.0.0/8"
    )
    # 是否记录出站访问（默认开）。关掉等于放弃 SC-009，仅用于排障时临时降噪。
    egress_record_enabled: bool = True

    # ===== 外部 MCP 工具（003-mcp-tool-market） =====
    # v1 只允许 Streamable HTTP；连接发生在 Worker，不在 API 请求处理中。
    mcp_transport: str = "streamable_http"
    mcp_connect_timeout_seconds: float = 5.0
    mcp_handshake_timeout_seconds: float = 10.0
    mcp_call_timeout_seconds: float = 30.0
    mcp_max_response_bytes: int = 262_144
    mcp_max_result_chars: int = 65_536
    mcp_max_tools_per_server: int = 200
    mcp_max_concurrency_per_server: int = 8
    mcp_allowed_ports: str = "443"
    mcp_require_https: bool = True
    mcp_max_redirects: int = 0
    # 003 的静态凭证密文由独立密钥保护；生产必须显式设置。
    mcp_credential_key: str = "dev-only-mcp-credential-key-change-me"

    # ===== 工具越权 =====
    # 同一 Run 内连续越权调用达到该次数即中止该步（data-model D-6）
    tool_deny_threshold: int = 5

    # ===== 指标（Prometheus）=====
    # `/metrics` 直接由 API 进程暴露，不经过 nginx（nginx 只反代 /api 与 /ws）。
    # 关掉后端点返回 404，适用于完全不想暴露指标的部署。
    metrics_enabled: bool = True
    # 可选：设置后抓取必须带 `X-Metrics-Token`，避免同网络内的任意容器读到指标。
    metrics_token: str = ""
    # Celery 默认队列名（读积压深度用）；改过 task_default_queue 时这里要同步
    celery_queue_name: str = "celery"

    # 僵尸 Run 回收阈值（Beat 周期任务）
    # queued 超过该分钟数仍未被执行 → 判定 Worker 未消费（消息丢失/Worker 未起）
    stale_queued_minutes: int = 30
    # running 超过该分钟数仍无进展 → 判定 Worker 执行中被杀（默认 max_run_seconds 1800，留余量）
    stale_running_minutes: int = 90

    # ===== 定时调度（004）=====
    # 扫描总开关：关闭后 Beat 仍会调用任务，但任务立即返回（便于排障时静默调度）
    schedule_scan_enabled: bool = True
    # 错过宽限期：计划时刻过去超过该秒数才计为"错过"。
    # 取 90s = 扫描周期 60s + 一次处理余量；取 0 会把每次正常延迟都记成错过。
    schedule_missed_grace_seconds: int = 90
    # 周期表达式的求值时区（该时区无夏令时，全年行为一致）
    schedule_default_timezone: str = "Asia/Shanghai"
    # 连续触发失败达到该次数即自动停用规则（沿用工具越权阈值的量级）
    schedule_max_failures: int = 5
    # 触发历史保留：每规则最多保留的条数（按 planned_at 倒序）
    schedule_history_max_rows: int = 500
    # 触发历史保留：最大保留天数
    schedule_history_retention_days: int = 90
    # 单次扫描最多处理的规则数（超出留到下一分钟，不丢弃）
    schedule_batch_size: int = 200

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_attachment_type_list(self) -> list[str]:
        return [
            t.strip() for t in self.allowed_attachment_types.split(",") if t.strip()
        ]

    @property
    def sbx_allowed_image_list(self) -> list[str]:
        return [i.strip() for i in self.sbx_allowed_images.split(",") if i.strip()]

    @property
    def egress_builtin_blacklist_list(self) -> list[str]:
        return [
            c.strip() for c in self.egress_builtin_blacklist.split(",") if c.strip()
        ]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in _PRODUCTION_ENVS

    @model_validator(mode="after")
    def _guard_sandbox_config(self) -> "Settings":
        """沙箱配置自检：错配的配额比没有配额更危险。

        - `SBX_IMAGE` 不在白名单 → 镜像校验形同虚设（改环境变量即可跑任意镜像）。
        - 超时/资源/并发 ≤0 → docker SDK 会把它解释成"无限制"（0 表示不限制），
          等于把宿主交给用户脚本。这些必须启动即失败。
        """
        problems: list[str] = []

        if self.sbx_image not in self.sbx_allowed_image_list:
            problems.append(
                f"SBX_IMAGE={self.sbx_image} 不在 SBX_ALLOWED_IMAGES"
                f"（{self.sbx_allowed_images}）内"
            )

        positive = {
            "SBX_TIMEOUT_DEFAULT": self.sbx_timeout_default,
            "SBX_TIMEOUT_MAX": self.sbx_timeout_max,
            "SBX_CPU": self.sbx_cpu,
            "SBX_MEM_MB": self.sbx_mem_mb,
            "SBX_DISK_MB": self.sbx_disk_mb,
            "SBX_PIDS": self.sbx_pids,
            "SBX_MAX_CONCURRENCY": self.sbx_max_concurrency,
            "SBX_STDOUT_MAX_BYTES": self.sbx_stdout_max_bytes,
            "SBX_CODE_MAX_BYTES": self.sbx_code_max_bytes,
            "SBX_ARTIFACT_MAX_FILES": self.sbx_artifact_max_files,
            "SBX_ARTIFACT_MAX_FILE_MB": self.sbx_artifact_max_file_mb,
            "SBX_ARTIFACT_MAX_TOTAL_MB": self.sbx_artifact_max_total_mb,
            "TOOL_DENY_THRESHOLD": self.tool_deny_threshold,
            "MCP_CONNECT_TIMEOUT_SECONDS": self.mcp_connect_timeout_seconds,
            "MCP_HANDSHAKE_TIMEOUT_SECONDS": int(self.mcp_handshake_timeout_seconds),
            "MCP_CALL_TIMEOUT_SECONDS": int(self.mcp_call_timeout_seconds),
            "MCP_MAX_RESPONSE_BYTES": self.mcp_max_response_bytes,
            "MCP_MAX_RESULT_CHARS": self.mcp_max_result_chars,
            "MCP_MAX_TOOLS_PER_SERVER": self.mcp_max_tools_per_server,
            "MCP_MAX_CONCURRENCY_PER_SERVER": self.mcp_max_concurrency_per_server,
            "SCHEDULE_MISSED_GRACE_SECONDS": self.schedule_missed_grace_seconds,
            "SCHEDULE_MAX_FAILURES": self.schedule_max_failures,
            "SCHEDULE_HISTORY_MAX_ROWS": self.schedule_history_max_rows,
            "SCHEDULE_HISTORY_RETENTION_DAYS": self.schedule_history_retention_days,
            "SCHEDULE_BATCH_SIZE": self.schedule_batch_size,
        }
        for name, value in positive.items():
            if value <= 0:
                problems.append(f"{name} 必须 > 0（当前 {value}）")

        if self.sbx_timeout_default > self.sbx_timeout_max:
            problems.append(
                f"SBX_TIMEOUT_DEFAULT（{self.sbx_timeout_default}）"
                f"不得大于 SBX_TIMEOUT_MAX（{self.sbx_timeout_max}）"
            )
        if self.sbx_artifact_max_file_mb > self.sbx_artifact_max_total_mb:
            problems.append(
                f"SBX_ARTIFACT_MAX_FILE_MB（{self.sbx_artifact_max_file_mb}）"
                f"不得大于 SBX_ARTIFACT_MAX_TOTAL_MB（{self.sbx_artifact_max_total_mb}）"
            )

        if problems:
            raise ValueError("沙箱配置不合法：" + "；".join(problems))
        return self

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> "Settings":
        """生产环境拒绝弱默认密钥（T010「启动时校验必需项」）。

        忘了配 JWT_SECRET 时用默认值跑生产 = 任何人可伪造 token（完全绕过鉴权），
        因此这里直接启动失败，而不是降级运行。
        """
        if not self.is_production:
            return self

        problems: list[str] = []
        if (
            self.jwt_secret in _WEAK_DEFAULT_SECRETS
            or len(self.jwt_secret) < _MIN_JWT_SECRET_LEN
        ):
            problems.append(f"JWT_SECRET（需 ≥{_MIN_JWT_SECRET_LEN} 字符且非默认值）")
        if (
            self.invite_code in _WEAK_DEFAULT_SECRETS
            or len(self.invite_code) < _MIN_INVITE_CODE_LEN
        ):
            problems.append(f"INVITE_CODE（需 ≥{_MIN_INVITE_CODE_LEN} 字符且非默认值）")
        if (
            self.mcp_credential_key == "dev-only-mcp-credential-key-change-me"
            or len(self.mcp_credential_key) < 32
        ):
            problems.append("MCP_CREDENTIAL_KEY（需 ≥32 字符且非默认值）")
        if problems:
            raise ValueError(
                f"APP_ENV={self.app_env} 下必须显式配置安全项：" + "、".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
