# 002 Docker 验证记录 — 2026-11-15

## 结论

**部分通过，不能宣布 002 完整验收完成或可上线。** 本轮执行了真实容器/内核/MinIO 探针，不再仅依赖 Docker SDK fake。受保护的 API→Celery→沙箱整链路仍被身份授权阻塞；domain allow、output 内核配额及 S6 等原规格缺口仍在。Quickstart S1–S6 继续保留 ⏳。

## 环境与隔离

- macOS 宿主上的 Docker Desktop 4.99.0；Docker Engine 29.9.3，API 1.56，Linux/arm64，内核 `7.0.14-linuxkit`。这是 **Docker Desktop Linux VM** 验证，不是原生 Linux 云主机验证。
- 已确认固定 helper 可访问 VM 内的 iptables、ip6tables、conntrack。
- 应用镜像构建成功。新建 `aw-002-verify` compose 项目，使用独立 PostgreSQL/Redis/MinIO、DNS、Mock 网关、API/Worker 和 8180 端口；不加载 `deploy/.env`、不使用开发数据卷。
- 既有 API/Worker 更新和网关 restart 被权限策略拒绝，未执行。原 `aw-api` / `aw-worker` / `aw-llm-gateway` 的 StartedAt 和运行中镜像 ID 最终检查保持不变。
- 在独立栈中创建验收管理员/成员邀请码的操作同样被权限策略拒绝，未执行，未借测试夹具或自签 token 绕过。
- 验收结束仅对新项目执行 `down`，未使用 `-v`，测试数据卷保留；该项目已无容器。既有集成测试服务和开发容器未停止。

## 实测暴露并修复的问题

| 提交 | 问题与修复 |
|---|---|
| `5c0e9b2` | 真实 SDK 的 `create()` 不接受 `remove` 参数；删除 run-only 参数。动态 IP 在启动前为空；改为预留 IPAM endpoint，保证先采集后执行。 |
| `615d05b` | FORWARD DROP 的 SYN 没有已确认 conntrack 事件，原方案漏 deny。改为 bridge 包头 watcher，等待 listening banner 再启动代码；修复 DNS 同 bridge established 回包和 cached DNS 归因。 |
| `81d925d` | 补齐独立 compose、确定性 Mock、HTTP 验收脚本、IP 出网探针、有界越权矩阵和真实 MinIO 下载 smoke。 |
| `c03ff59` | SDK wait 不刷新 attrs，OOMKilled 一直可能是创建时值；执行退出后 reload，再读取 daemon 状态。真实有界分配已验证 `OOMKilled=true`。 |

## 真实运行时结果

### 脚本与制品

- 平方和脚本输出 `338350`，退出码 0；最初修复后的直接调用含准备/回收约 4.44 秒，进程耗时 54ms。不能把这一条低层探针视为所有完整链路均满足 SC-001；后续一些网络探针准备/回收本身超过 5 秒。HTTP harness 已改用 `finished_at - created_at` 验证完整执行窗口，而不是只检查进程 `duration_ms`。
- 真实 Worker→一次性沙箱写 `sales.csv`→`artifact_service.put_bytes`→真实 MinIO→预签名 HTTP GET：20 行数据、153 字节，下载字节与源制品完全一致。
- SHA-256：`1ed34dd6a0f8c9a56ed2d3baed080aa9516d3d09e47131db490f1a935e622eb3`。
- 复现脚本：`deploy/verify/s4_artifact_runtime.py`；最后一次执行 ID `e6b89643-3d87-41a3-b244-7f1e3b695836`。测试对象已按其唯一 key 删除。
- 这不是 Run/Step/Artifact 数据库归属、API 下载越权或制品上限的真实 HTTP 验收。

### 出网 IP 策略：7/7 通过

`deploy/verify/s5_egress_scenarios.py --public-url https://pypi.org`，使用本次新建 DNS proxy。IP 测试目标为脚本自己创建的 HTTP fixture（`198.18.254.2:8000` / `10.253.254.2:8000`），不是扫描其他服务。元数据只尝试 TCP 握手，不发送 HTTP 请求或读取数据。

| 场景 | 实际结果 |
|---|---|
| default 直连 IP | 连接成功，`ip-direct allow` |
| 内置私网 deny | 连接失败，目标 `deny` 留痕 |
| `169.254.169.254:80` | 连接失败，`deny` 留痕 |
| restricted 显式 IP allow | 成功，记录 allow 规则 UUID |
| restricted 空规则 | 失败，`deny` 留痕 |
| 全局 deny 覆盖 Agent allow | 失败，记录全局 deny UUID |
| 默认 HTTPS/DNS | pypi.org HTTPS 成功，记录 `pypi.org:443 allow`，DNS 例外标 `dns-proxy` |

全部 7 次执行的容器、网络、卷残留计数均为 0。该结果不证明 domain allow、规则 CRUD/权限 API、IPv6 审计或 SC-009 100% 覆盖；内置规则的 matched_rule_id 仍为 null。

### 有界越权与资源矩阵：12 项通过，1 项明确失败

`deploy/verify/sbx_escape_matrix.py` 最终**退出码 1**，不是全绿。

| 检查 | 结果 |
|---|---|
| 宿主目录 / Docker socket 不可见 | PASS |
| 应用凭据环境变量不存在 | PASS，不输出任何凭据值 |
| 元数据 / 私网 TCP | 两项 PASS，均有 deny 记录 |
| 有界创建子进程 | PASS，126 个子进程后触及 pids 限额，未在宿主 fork |
| 有界内存分配 | PASS，退出 137、daemon `OOMKilled=true`、resource/oom |
| workspace tmpfs | PASS，在 268435456 字节处 ENOSPC |
| 路径穿越写 rootfs | PASS，被只读/权限边界拒绝 |
| 非 root / cap / no-new-privileges / setuid | PASS |
| detached 后台子进程 | PASS，执行容器整体回收 |
| 2 秒 timeout | PASS，在 3 秒额外容差内终止 |
| 执行取消 | PASS，category=cancelled，整体回收 |
| **output 卷的内核磁盘配额** | **FAIL：普通 named volume 无大小上限；未为测试而写满宿主盘** |

12 次实际执行的容器/卷/网络残留计数均为 0。另对记录中 41 个执行 UUID 的 IPv4/IPv6 私有链做了只读核查，残留链为 0。此矩阵证明已列出的有界尝试被边界阻止，不代表完整安全审计、全部 failure category 的 UI 验证或 SC-002 总体通过。

### 应用 HTTP 范围

- 新独立栈 `/api/v1/health/ready`：200；匿名 `/api/v1/tools`：401。
- `s3_sandbox_scenarios.py` 已实现 owner/other/admin 凭据驱动的 S1/S3/S4 检查、真实执行详情/指纹、失败修正、制品下载 checksum、跨 owner 404、tool_denied 审计和 tools=[]。
- **本轮未执行这些受保护场景**：没有获批的验收身份，创建管理员/邀请码被拒。脚本自身 13 项测试通过只是 harness/Mock 协议测试，不替代真实应用链路。

## 回归门禁

- 最终后端全套：**520 passed in 58.13s**。仅使用专用 `aw-002-integration-postgres`（55432 的 agent_workbench_test）、Redis 56379、MinIO 59000；测试 schema 重建不指向开发库。
- `ruff check app`：通过；`mypy app`：84 source files，通过。
- 验收 harness 自测：13/13 通过。
- 本轮没有修改前端；此前前端 32 项、typecheck/build 已通过，ESLint 0 errors / 2 既有 warnings。本轮未重复宣称完成浏览器 E2E。

## 继续验收所需条件与剩余缺口

1. 需要明确授权在独立验收栈预置测试管理员/成员邀请码，或提供该栈已存在且获准使用的 owner/other/admin 凭据；不需要因此授权修改现有开发服务。
2. 完成 protected API→Celery→真实沙箱→持久化/审计/下载与 WS 验收。
3. 修复 output 卷内核配额；完成 domain→内核规则安全绑定、完整 IPv6/访问覆盖、错误分类/事件契约收口。
4. 完成五种工具真实可用性、制品上限、存量迁移/历史快照、20 并发与 P95、原生 Linux 目标环境验收。

`.specify/extensions.yml` 不存在，本轮无 after_implement hook 可执行。
