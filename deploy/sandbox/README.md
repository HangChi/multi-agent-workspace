# 沙箱执行镜像 `aw-sandbox-python:3.12`

用户提交的 Python / shell 脚本在这里运行。**它不是给终端用户直接用的镜像**，只由
Worker（`backend/app/executors/sandbox_client.py`）以一次性容器方式拉起。

## 为什么单独一个镜像

| 决策 | 理由 |
|---|---|
| 非 root（UID/GID 10001） | 容器内没有提权路径；Worker 侧按数字校验，不认用户名 |
| 不设 ENTRYPOINT | 启动命令是服务端常量（`python -u /code/main.py` / `bash /code/run.sh`），脚本注入独立有界卷，用户侧只读；脚本内容绝不拼命令行 |
| 只回收 `/workspace/output` | 产物出口单一，避免把临时文件、依赖缓存一起拖回来 |
| 预装 numpy/pandas/matplotlib/requests/bs4/openpyxl | 高频库预装，省掉每次执行 30s 的安装开销（会把时长配额吃光） |
| 不持久化 pip 缓存 | 可写层跨执行保留 = 上一次的恶意包会污染下一次，与「一次性」语义冲突 |

运行期仍可 `pip install`（出网默认放行），但每次执行都从干净层开始。

## Worker 施加的运行时边界

- `/workspace`（含 `output/`）、`/tmp` 与 `/dev/shm` 是同一个 `size=` 受限的 local-driver tmpfs 卷，合计受 `SBX_DISK_MB` 限制，不使用无上限的普通 output 磁盘卷。
- 无网络、只读 rootfs、无 capabilities 的短命存储 keeper 保持卷挂载；用户进程退出后从 keeper 回收制品，再删除执行容器、keeper、卷和网络。tmpfs/keeper 准备失败时拒绝执行，不降级到普通卷。
- 用户容器关闭 Docker 日志落盘；启动前建立 attach，逐流保留有限尾部和原始字节计数，实时发布另受预算约束，避免输出洪水撑爆 Worker 或事件通道。
- 资源采集在用户进程存活时进行，避免退出后 cgroup 被清空导致假 0；缺失时为 NULL。无法取得内核 lifetime peak 时，内存值为已观察样本的最大值，不冒充精确 lifetime peak。
- 用户脚本只能在沙箱容器执行；Docker 参数与可信辅助命令均由服务端固定，调用方不可任意提供。

## 重建

```bash
# 在仓库根目录
docker build -t aw-sandbox-python:3.12 deploy/sandbox
```

⚠️ **tag 必须与后端 `SBX_IMAGE` / `SBX_ALLOWED_IMAGES` 一致**。配置校验器会在启动时
比对，不一致直接拒绝启动（防止有人改环境变量就跑任意镜像）。

## 自检

```bash
# 1) 非 root
docker run --rm --entrypoint id aw-sandbox-python:3.12
# 期望：uid=10001(sandbox) gid=10001(sandbox)

# 2) 预装库可用
docker run --rm --entrypoint python aw-sandbox-python:3.12 -c "import numpy,pandas,matplotlib,requests,bs4,openpyxl; print('ok')"

# 3) 产物目录可写
docker run --rm --entrypoint python aw-sandbox-python:3.12 -c "open('/workspace/output/a.txt','w').write('x'); print('ok')"

# 4) 误用即退出（不设 ENTRYPOINT 的兜底）
docker run --rm aw-sandbox-python:3.12; echo "exit=$?"
# 期望：exit=2，stderr 提示 launch via worker only
```

## 已知边界

- **不含 GPU、不含浏览器**（v1 范围外，spec「不含」已列）。
- **不是内核级隔离**：只有命名空间 + cgroup + 资源限额。seccomp/gVisor 归 P3 加固
  （宪法 Follow-up TODO），本镜像不做。
- 镜像体积约 1.1GB（pandas + matplotlib 占大头）。部署时随 compose 构建，不重复拉取。
