# 后端镜像（T081）：多阶段构建、非 root 运行、密钥不落镜像。
#
# 密钥策略：镜像内不烧录任何密钥/口令。所有敏感配置（DATABASE_URL、
# REDIS_URL、JWT_SECRET、MINIO_*、LLM_API_KEY 等）在运行时通过
# 环境变量注入（docker compose `environment:` / `env_file:`）。
#
# 依赖策略：用 uv.lock 而不是 `pip install .`。
# `pip install .` 只读 pyproject 的版本区间，每次构建可能解析出不同版本 ——
# 「本地跑得好好的，镜像里行为不一样」大多来自这里，而且不可复现。
# `uv sync --locked` 严格按 lock 安装，lock 与 pyproject 不一致时**直接失败**
# （而不是悄悄重新解析），这正是我们要的确定性。

# ---------- Stage 1: 依赖构建（仅安装非 dev 依赖） ----------
FROM python:3.12-slim AS deps

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /opt/aw

# uv 官方镜像按 digest 固定：tag 会移动，digest 不会。
# 想升级就显式改这一行 —— 让「换构建工具版本」成为一次可见的改动。
#
# 版本必须能读懂 uv.lock 的 revision（见 lock 文件头 revision = N）。
# 旧版 uv 读到新 revision 会直接报错，而不是降级容忍 —— 所以这里与
# 本地开发用的版本保持一致，避免「本机能 lock，镜像装不了」。
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /usr/local/bin/uv

# 先只复制依赖清单，利用 Docker 层缓存：代码改动不会让依赖重装。
# uv sync 需要项目元数据（name/version/deps）才能建 venv，
# 所以 pyproject.toml + uv.lock 必须一起进来。
COPY pyproject.toml uv.lock ./

# --no-dev：镜像里不要测试/静态检查依赖
# --no-install-project：此时 app/ 还没复制进来，先只装第三方依赖
RUN uv sync --locked --no-dev --no-install-project

# 应用代码（必须在依赖装好之后复制，否则任何代码改动都会让上面那层缓存失效）
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

# 现在把项目自身装进 venv（--no-editable：镜像里要的是稳定快照，
# 不要指向源码目录的 .pth，否则运行时会读到意外文件）
RUN uv sync --locked --no-dev --no-editable

# ---------- Stage 2: 运行时（非 root） ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # venv 在 PATH 最前：uvicorn/celery 入口由 uv 装进 .venv/bin
    PATH="/opt/aw/.venv/bin:$PATH"

WORKDIR /opt/aw

# 非 root 运行：创建低权限用户；应用目录归 app 所有（beat 需写 celerybeat-schedule）
RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --shell /usr/sbin/nologin --home-dir /opt/aw app && \
    chown -R app:app /opt/aw

# 整个 /opt/aw 从 deps 阶段复制：包含 .venv（依赖）与 app/alembic（代码）
COPY --from=deps --chown=app:app /opt/aw /opt/aw

USER app

# 默认启动 API（compose 中 worker/beat 用 command 覆盖）
# --no-access-log：访问日志由 RequestContextMiddleware 输出（带 request_id/耗时），
# 关掉 uvicorn 自带的重复行
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]
