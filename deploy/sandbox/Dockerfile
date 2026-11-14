# syntax=docker/dockerfile:1
# 沙箱执行镜像（002-sandboxed-tools，research R-08）：用户脚本在这里跑。
#
# 设计约束（改动前先读 specs/002-sandboxed-tools/research.md 的 R-01/R-05/R-07/R-08）：
# - **非 root**：UID/GID 固定 10001，容器内没有提权路径。
# - **不设 ENTRYPOINT**：启动命令由服务端代码固定为
#   ["python", "/workspace/main.py"] 或 ["bash", "/workspace/run.sh"]；
#   脚本内容以文件写入（put_archive），绝不拼进命令行（R-07 的注入面收敛）。
# - **/workspace/output 为唯一产物出口**：Worker 只回收该目录（R-05）。
# - **不持久化 pip 缓存**：可写层跨执行保留 = 恶意包横向污染（R-08）。
# - 僵尸进程回收靠 `containers.run(init=True)`（Docker 内置 docker-init 作 PID 1），
#   镜像内不再装 tini。
#
# 重建：
#   docker build -t aw-sandbox-python:3.12 deploy/sandbox
#
# ⚠️ tag 必须与后端 SBX_IMAGE / SBX_ALLOWED_IMAGES 一致，否则容器被拒绝启动
#    （见 backend/app/core/config.py 的 _guard_sandbox_config）。

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/.mpl \
    HOME=/workspace

# 系统层：只留脚本常用且体积可控的命令行工具。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Python 层：预装高频库，避免每次执行都花 30s 装 pandas 把时长配额吃光。
# 运行期仍允许 `pip install`（默认放行出网），但不跨执行缓存。
RUN pip install --no-cache-dir \
        numpy \
        pandas \
        matplotlib \
        requests \
        beautifulsoup4 \
        openpyxl

# 非 root 用户。UID/GID 固定：Worker 侧按数字校验，不依赖用户名解析
# （镜像被替换后用户名可能相同而 UID 不同）。
RUN groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid sandbox --shell /usr/sbin/nologin --no-create-home sandbox

# 工作目录与产物出口。output/ 必须预先建好并归属 sandbox，
# 否则在只读根文件系统下非 root 用户无法创建它。
RUN mkdir -p /workspace/output \
    && chown -R sandbox:sandbox /workspace

WORKDIR /workspace
USER sandbox

# 故意不设 ENTRYPOINT。若有人误用 `docker run <本镜像>`，会立刻以非零码退出，
# 不会留下常驻进程（也避免"以为在跑其实没跑"的假象）。
# 注意：exec 形式的 JSON 数组里不要用 \n 之类的转义，Dockerfile 解析不会还原它们。
CMD ["python", "-c", "import sys; sys.stderr.write('sandbox image: launch via worker only'); sys.exit(2)"]
