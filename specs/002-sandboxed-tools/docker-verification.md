# 002 Docker 验证记录 — 2026-11-15

## 结论

**部分通过，不能宣布 002 完整验收完成或可上线。** 下文保留历史失败快照；最新本地门禁为后端 703 项、前端 43 项，Docker Desktop Linux VM 的 I/O 2/2、越权矩阵 13/13、域名专项 18/18。原生 GitHub CI 已在全新隔离栈实际执行迁移和受保护 HTTP 用例，当前不再是仅有身份阻塞：run `43699286675` 因共同沙箱准备故障及验收 harness 缺陷失败，后续 S6 等未执行。已提交 `f698fe5` 修复真实 WS/结果读取、fixture readiness 和有界脱敏诊断，仍须原生重跑取得通过证据。Quickstart S1–S6 继续保留 ⏳。

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

## 2026-11-15 后续修复与复验（最新）

前文 520 项、7/7 出网与 12 项矩阵是当时的历史结果，保留原状，不代表当前最新验证。此后本地提交 `2ed08f3`、`3e17dcc`、`668299d`、`1e3b571` 分别补充删除规则强制审计、沙箱 I/O 与出网事实边界、制品/API 提示，以及出网筛选/Agent 保存 warning 契约。

- 全套后端 pytest：**602 passed**；`ruff check app`、`mypy app`（84 files）通过。
- 前端 Vitest：**43 passed**；typecheck/build 通过；ESLint 0 errors、2 个既有 Fast Refresh warnings。验收 harness：13 项通过。
- `deploy/verify/s2_io_scenarios.py`：**2/2**。真实有界 tmpfs 在 16 MiB 配额处 `ENOSPC`；`/workspace` 与 `/tmp` 共享额度、`/code` 只读；执行退出后 keeper 可回收制品。多 MB stdout/stderr 实测每流精确保留 4096 bytes、原始计数准确、live callback 受 4096-byte 预算限制；执行资源残留 0。
- `deploy/verify/sbx_escape_matrix.py`：**13/13**，包含 output 内核配额、timeout/cancel 等；每项执行后容器/网络/卷残留 0。
- `deploy/verify/s5_egress_scenarios.py`：**11/11**，包含同目标 3 条独立允许与拒绝 TCP 连接、真实 IPv6 源地址 allow/restricted deny/global deny 优先；fixture 与执行资源清理。该轮 11 项不含可选 public HTTPS 场景；未把早先版本的 HTTPS 结果算成本轮最终代码验证。
- Docker 证据仍仅适用于 macOS 上的 Docker Desktop Linux VM，不代表原生 Linux 云主机。domain allow/global domain deny 继续 fail-closed；protected API→Celery→真实 Docker/DB/WS、20 并发/P95、全量协议与高负载访问覆盖均未完成。

工作区测试使用专用 integration PostgreSQL/Redis/MinIO；未读取 `deploy/.env`，未重启既有开发服务，未创建或伪造验收身份。尝试运行本轮可选公网 HTTPS smoke 时被权限策略拒绝，因此未执行、不记作通过。

## 上轮验收条件与缺口（历史快照，已由下节进展更新）

当时受保护身份不可用，output 内核配额和域名内核绑定未完成，完整工具、制品、迁移历史及 S6 尚待验收。此为历史状态，不代表当前实现仍缺少这些功能；原始失败证据保留在上文。

## 2026-11-15 原生 GitHub CI 与最终本地门禁

- `4330b63`：数据库 enabled 目录 gate、工具撤权与不可变运行授权快照，真实 PostgreSQL 测试；历史运行保留 SQL NULL，不按当前配置补写。
- `937d61c`：执行私有 DNS/TTL/端口内核绑定、pre-defrag 协议 guard、AF_PACKET 审计，以及 `/dev/shm` 与 workspace/tmp 共用有界卷。域名专项 VM 18/18，含 TTL、CNAME、IPv6/UDP、deny 优先、helper 死亡/挂起及残留检查。
- 最终本地后端 **697 passed in 65.78s**，Ruff app/tests 和 Mypy 85 文件通过；前端 **43 passed**、typecheck/build 通过，ESLint 0 errors/2 个既有 warnings。最终实际 I/O **2/2**、越权矩阵 **13/13**。这些是 Desktop Linux VM 或专用测试库证据，不冒充原生整栈成功。
- GitHub run `43698802253`（`937d61c`）：独立前后端门禁与全部镜像构建通过，原生前置检查因 bridge netfilter 未启用失败；未进入应用验收。`2171e70` 在临时 runner VM 明确加载 br_netfilter 并启用双族过滤，入口独立读取两个 sysctl，未绕过检查。
- GitHub run `43699286675`（`2171e70`）：原生 Ubuntu/Docker 28、cgroup v2，双族 bridge/netfilter 前置实测通过；真实迁移、工具目录与旧历史 SQL NULL 校验通过。HTTP 14 项仅 denied/no_tools 通过，I/O 2 项及越权矩阵 13 项失败；S5 fixture 未产出报告导致 runner 中止，域名、S6 与 fault suites 未执行。执行资源和本次 project 清理通过。不可把后续未执行项目算成功。
- `f97012d`、`f698fe5`：失败诊断仅保留有限 stage/code/type/HTTP status；修复 harness 把 summary 当工具输出、错误 WS 路径、取消观察顺序，以及 fixture listener readiness/setup/cleanup 报告。全量 harness 自测 **61/61**、scoped Ruff/diff check 通过；诊断重跑不能替代实际故障修复。

### 第三轮原生诊断与 Docker 28 兼容性修复

- Run `43701273368`（`f698fe5`）：前后端门禁通过，真实迁移与历史保留通过；HTTP denied/no_tools/builtins/denial_threshold 通过，其余 10 项失败。I/O 2 项和其余 SandboxRun 场景的共同原因已明确：`container_create` / `static_ip_requires_subnet` / daemon HTTP 400，不再只是推测 IPv6 pool 或权限问题。域名专项在首项之前异常，原因仍待其独立 phase 诊断，不能套用 SandboxRun 根因。
- 本轮所有套件实际有报告：S5 IP+IPv6+HTTPS 12 项均失败，域名专项未完成 18 项；infra fault 通过，quota 未通过。严格 S6 baseline P95 **37.060ms**、load P95 **195.697ms**、比例 **5.2806**，20 次运行全部失败、活容器峰值 0；此结果明确不通过，未剔除样本。独立 observer 的两个错误均为 `NotFound`（SDK list 后逐个 inspect 与回收竞争）。持久化/审计不变量与执行/project/内核残留检查通过，残留 0。
- `d88890d`：保留 Docker 自动选择子网，先创建本次 UUID 的空双栈网络读取真实 subnet/gateway，再仅删除该 reservation 并以相同地址池显式重建；只对已确认的子网重叠冲突重试，最多 8 次。始终保留 IPv6、固定安全参数、执行前静态地址预留、策略与抓包，不选任意宿主子网、不降级动态端点、不修改 Docker API 协商以绕过验证。
- 修复后本地后端 **703 passed，3 subtests passed in 65.78s**，仅使用本次新建的随机凭据、随机 loopback 端口一次性 PostgreSQL，结束后仅删除该容器；既有测试库 schema 重建被权限策略拒绝后未重试。真实 I/O **2/2**、四个同时执行的独立双栈沙箱均成功且无残留。
- `4b9e7ac`：observer 改为单次 daemon 活容器快照，不因已回收容器的后续 inspect 丢失采样；真实查询/结构错误仍失败。域名专项保留全部 18 项、强化有限阶段诊断和独立清理；完整 harness **80/80**、Ruff/diff check 通过。修改后的真实 VM 域名专项再次 **18/18**，含内核/容器/网络残留 0。
- 原生重跑 `43703079200`（`6195f7b`）正在执行。所有本地或 harness 通过均不等于该原生重跑通过。

剩余门禁：原生完整 HTTP/I/O/越权/IP+IPv6+HTTPS/域名矩阵、SC-001 完整窗口、严格 20 并发 P95，以及 infra/quota fault suites 全部通过后，才能完成 T068/T069/T076。只上传有界脱敏 JSON，不输出或提交 `deploy/.env` 凭据，不重启既有本地开发服务。

`.specify/extensions.yml` 在上轮检查时不存在；最终完成前仍需复核 after_implement hooks。
