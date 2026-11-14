# syntax=docker/dockerfile:1
# 后端镜像（T081）：多阶段构建、非 root 运行、密钥不落镜像。
#
# 密钥策略：镜像内不烧录任何密钥/口令。所有敏感配置（DATABASE_URL、
# REDIS_URL、JWT_SECRET、MINIO_*、LLM_API_KEY 等）在运行时通过
# 环境变量注入（docker compose `environment:` / `env_file:`）。

# ---------- Stage 1: 依赖构建（仅安装非 dev 依赖） ----------
FROM python:3.12-slim AS deps

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/aw

# 先复制 pyproject.toml，利用 Docker 层缓存：依赖不变时跳过 pip install
COPY pyproject.toml ./
RUN pip install --no-cache-dir "uvicorn[standard]" && \
    pip install --no-cache-dir "celery[redis]" && \
    pip install --no-cache-dir .

# ---------- Stage 2: 运行时（非 root） ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/aw

# 非 root 运行：创建低权限用户；应用目录归 app 所有（beat 需写 celerybeat-schedule）
RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --shell /usr/sbin/nologin --home-dir /opt/aw app && \
    chown -R app:app /opt/aw

# 从 deps 阶段复制已安装的 site-packages 与可执行入口（uvicorn/celery）
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# 应用代码
COPY app ./app

# 数据库迁移（migrate 一次性服务需要 alembic 配置与版本脚本）
COPY alembic.ini ./
COPY alembic ./alembic

USER app

# 默认启动 API（compose 中 worker/beat 用 command 覆盖）
# --no-access-log：访问日志由 RequestContextMiddleware 输出（带 request_id/耗时），
# 关掉 uvicorn 自带的重复行
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]
