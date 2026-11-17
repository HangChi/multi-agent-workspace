# syntax=docker/dockerfile:1
# 生产 MinIO 镜像：不依赖 Docker Hub 的 minio/minio（该仓库已不可拉取）。
# 详见同目录 README.md 与 install_minio.py。
FROM python:3.12-slim

ARG TARGETARCH

COPY install_minio.py /tmp/install_minio.py
RUN python /tmp/install_minio.py && rm -f /tmp/install_minio.py

# 非 root 运行（与 api/worker 的 10001、前端的 10002 区分开）
RUN useradd --uid 10003 --no-create-home --shell /usr/sbin/nologin minio \
    && mkdir -p /data \
    && chown minio:minio /data

LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="07c3a429bfed433e49018cb0f78a52145d4bedeb" \
      org.opencontainers.image.licenses="AGPL-3.0-only"

USER 10003:10003

EXPOSE 9000 9001

# 健康探针用 python（基础镜像自带），不额外装 curl
HEALTHCHECK --interval=10s --timeout=3s --retries=10 --start-period=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000/minio/health/live',timeout=2)"]

ENTRYPOINT ["minio"]
CMD ["server", "/data"]
