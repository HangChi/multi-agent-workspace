# MinIO 服务镜像（生产）

Docker Hub 上的 `minio/minio` 仓库已不可拉取，而 `deploy/docker-compose.yml`
原先直接引用 `minio/minio:RELEASE.2023-03-20T20-16-18Z`。本机之所以能
`up -d`，只是因为那层镜像还留在本地缓存里 —— 换一台新机器部署会在拉取阶段失败。

这里改为**自建镜像**：从 GitHub Release 下载不可变制品，并用发布页公布的
SHA-256 全量校验。校验不通过时构建直接失败，没有「回退到官方镜像」的分支。

- 发行版：<https://github.com/minio/minio/releases/tag/RELEASE.2025-09-07T16-13-09Z>
- 许可证：AGPL-3.0，随二进制落到 `/usr/share/licenses/minio/LICENSE`
- 基础镜像只用来跑下载脚本与健康探针，不引入 curl 等额外工具

## 升级步骤

1. 改 `install_minio.py` 里的 `RELEASE`、两个架构的 `DIGESTS`、`LICENSE_COMMIT`
   与 `LICENSE_DIGEST`（与发布页/API 返回值逐字对齐）。
2. `deploy/docker-compose.yml` 里 `image:` 的 tag 同步改成新版本（便于回滚识别）。
3. `docker build -t aw-minio:<RELEASE> deploy/minio` 先本地构建一次，确认校验通过。

## 与 CI 验收镜像的关系

`deploy/verify/minio.Dockerfile` 是 CI 验收专用（同一套下载+校验逻辑，只是写在
Dockerfile 内联脚本里）。两者刻意保持独立：验收镜像改动不应影响生产镜像的可复现性，
反之亦然。升级发行版时请同时更新两处，否则 CI 与生产会跑在不同版本上。
