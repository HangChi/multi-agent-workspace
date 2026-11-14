# 沙箱执行镜像 `aw-sandbox-python:3.12`

用户提交的 Python / shell 脚本在这里运行。**它不是给终端用户直接用的镜像**，只由
Worker（`backend/app/executors/sandbox_client.py`）以一次性容器方式拉起。

## 为什么单独一个镜像

| 决策 | 理由 |
|---|---|
| 非 root（UID/GID 10001） | 容器内没有提权路径；Worker 侧按数字校验，不认用户名 |
| 不设 ENTRYPOINT | 启动命令是服务端常量（`python /workspace/main.py` / `bash /workspace/run.sh`），脚本内容只以文件写入，绝不拼命令行 |
| 只回收 `/workspace/output` | 产物出口单一，避免把临时文件、依赖缓存一起拖回来 |
| 预装 numpy/pandas/matplotlib/requests/bs4/openpyxl | 高频库预装，省掉每次执行 30s 的安装开销（会把时长配额吃光） |
| 不持久化 pip 缓存 | 可写层跨执行保留 = 上一次的恶意包会污染下一次，与「一次性」语义冲突 |

运行期仍可 `pip install`（出网默认放行），但每次执行都从干净层开始。

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
