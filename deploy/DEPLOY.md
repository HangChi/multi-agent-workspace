# 部署

```bash
deploy/deploy.sh --check     # 只做前置检查，不动容器
deploy/deploy.sh             # 构建 → 迁移 → 换容器 → 健康门禁（失败自动回滚）
SKIP_BUILD=1 deploy/deploy.sh   # 镜像已就绪（CI 构建产物 / 只想重启）
```

## 顺序与理由

1. **先迁移**（`migrate` 一次性容器，跑完才继续）。反过来会出现「新代码读到旧表结构」的窗口。
2. **换服务**：`up -d --no-deps api worker beat frontend`，不碰数据库/Redis/MinIO。
3. **健康门禁**：只认 `GET /api/v1/health/ready`（它同时探 DB 与 Redis）。容器起来不等于能服务。
4. **worker/beat** 不在 HTTP 路径上，单独检查 `running` 与重启次数（启动即崩会体现为反复重启）。
5. **失败自动回滚**：部署前把当前镜像打成 `aw-<service>:previous`，健康门禁失败就重新 tag 回去并用
   `--no-build --pull never` 拉起 —— 回滚路径绝不联网。

## 迁移不回滚

脚本不会自动 `alembic downgrade`：降级会丢数据，这个决定不能由脚本替你做。
因此迁移必须保持**向后兼容**：加列先给默认值、删列留到下一版、改类型走「加新列 + 双写 + 切换」。

## CI 里怎么用

`workflow_dispatch` 带 `deploy=true` 时才出现 `deploy` job（合并到 master 不等于可以上线）。
需要 4 个 secret：

| secret | 说明 |
|---|---|
| `DEPLOY_HOST` | 主机地址（`ssh-keyscan` 会固定指纹，不用 `StrictHostKeyChecking=no`） |
| `DEPLOY_USER` | 专用部署账号，**不要复用个人账号** |
| `DEPLOY_SSH_KEY` | 该账号的私钥 |
| `DEPLOY_PATH` | 仓库在主机上的路径，缺省 `~/agent-workbench` |

远端执行的就是本脚本，所以 CI 与手工部署走的是同一条路径。

## 实测记录（2026-11-17，本机 Docker Desktop）

```
[deploy] 已保存回滚点：aw-api:previous ← aw-api
[deploy] 迁移完成
[deploy] 健康检查：http://127.0.0.1:8080/api/v1/health/ready（最长 120s）
[deploy] 健康检查通过
[deploy] worker: status=running restarts=0
[deploy] beat: status=running restarts=0
[deploy] 部署完成
```

`--check` 在缺少 `.env` 时会直接失败——这是有意的：生产密钥不走 git，
缺 `.env` 说明这台机器还没初始化好。

## 已知限制

- 单机 compose 的**滚动**是假的：`up -d` 会重建容器，期间有秒级中断。
  真正做到零中断需要多副本 + 反代摘流，属于换部署形态（k8s/swarm），不在当前范围。
- 回滚只回镜像，不回数据库。迁移有破坏性变更时必须人工介入。
