# Research: 沙箱代码执行与工具授权（Sandboxed Tools）

**Phase 0 产出**：技术决策论证。spec 无 NEEDS CLARIFICATION，本文档论证 10 项关键实现路径，其中 R-02（出站可见性）是 spec 阶段标记的最高风险项，在此定案并记录已知缺口。

## R-01 沙箱的隔离与生命周期由谁管理

**Decision**: 复用宿主 Docker 守护进程，Worker 通过 Docker SDK（`docker` Python 包）以「一次性容器」方式创建/执行/销毁沙箱，Worker 容器挂载 `/var/run/docker.sock`。

**Rationale**: 宪法已定「Docker 一次性容器」为沙箱形态，不引入第二套隔离设施。Docker 参数和入口 argv 均为服务端常量；用户脚本只作为数据写入独立有界 `/code` 卷，用户侧只读，不拼进宿主命令。CPU/内存/PID、只读 rootfs、capabilities 与卷上限由服务端固定。SDK 的 attach、wait、SIGKILL 和 archive 接口支持有界采集与回收。

2026-11-15 的真实生命周期：受限卷/无网络 keeper 准备并写入脚本 → 独立网络与内核策略 → `containers.create()` → 包头 watcher/输出 attach 就绪、检查取消和策略健康 → `start()` → 存活期有界输出/资源采样 → 退出后从 keeper 回收产物 → finally 删除用户容器、helper、卷、规则与网络。`/workspace`、`/tmp`、`/dev/shm` 共用同一受限 tmpfs，避免独立 output 或默认 shm 绕过总磁盘预算。Docker 用户容器日志落盘关闭，不能以事后截断替代运行期有界性。

**Alternatives considered**:
- Docker-in-Docker（`--privileged` + 内部 dockerd）：隔离更强，但 Worker 需特权模式，且镜像层重复拉取，单机内存开销翻倍；宪法的「资源上限」约束下不划算。
- `subprocess` 调 `docker run`：需要拼参数与解析 stdout，注入面与脆弱性都更高；`get_archive` 类流式能力缺失。
- gVisor / Kata：宪法明确留到 P3 加固，本特性不做，但 `sandbox` 执行器的镜像与参数是集中配置的，后续替换运行时只需改一处。

**风险与缓解**：挂载 socket 等价于给 Worker 宿主 root 权限。缓解：① 沙箱容器参数由服务端代码固定，**不接受**任何来自用户/LLM 的 docker 参数；② 镜像白名单（`SBX_ALLOWED_IMAGES`）硬编码在配置，运行时校验；③ 网络固定 `bridge` + 独立自定义网络，不允许 `host`/`none` 之外的自选模式；④ 该风险在 P3 安全复核中作为已知项记录。

## R-02 出站可见性与黑名单强制（spec 标记的最高风险）

**Decision（2026-11-15 实现修订）**：内核强制 + 执行私有 DNS 绑定 + pre-defrag 包头事实，不做 TLS 解密。

1. **内核层**：独立 bridge 与固定 argv-only 批处理 helper 安装/回收 iptables/ip6tables 规则和执行私有 nft 表。所有执行均有 `PREROUTING priority -450` 协议 guard，在 conntrack defrag 之前拒绝分片、IPv6 扩展链和不支持的路由协议。已真实复现 FORWARD 才检查分片会被预先重组绕过，不能退回该方案。
2. **域名层**：存在域名策略时启动执行私有可信 mediator，仅监听本执行 bridge gateway:53；共享 dnsmasq 只是上游。原始域名和完整有界 CNAME 链都检查黑名单，拒绝私网/元数据/转换地址重绑定。DNS 答复前在单个 nft 事务内安装按规则、IP 族、端口及 TTL 隔离的集合；原始 TTL 不因链查询/锁等待而重置。全局 deny 优先，当前 TTL/健康状态对 established 流仍有效。
3. **失效边界**：内核健康租约 ≤1 秒；Worker 再检查 Running/Paused 和可信提交后心跳，识别死亡及 SIGSTOP/挂起。不可验证、截断、过短 TTL、上游/内核失败或请求/绑定/审计预算耗尽均 fail-closed。默认模式含全局域名 deny 时，未受控解析绑定的直连 IP 也拒绝。
4. **记录层**：固定 AF_PACKET watcher 在用户进程前就绪，内核经典 BPF 过滤源 IP，再校验 execution bridge-master/veth ingress，只解析固定包头并使用内核时间戳。TCP 按连接代际/源端口区分，UDP 按五元组流归并；无端口拒绝事实记录 `port=0`。丢包、日志截断、事件上限或非正常 helper 退出不得伪装成完整成功审计。DNS 拒绝记录真实 DNS 传输目标/端口，不伪造未发生的后续连接。

**被替代的原计划**：执行后 `conntrack -L` 快照无法覆盖已被 DROP 的 SYN、已消失的短连接或 pre-defrag 事实，已弃用；共享 DNS 日志也不能提供执行级安全授权。`bytes_out` 当前保持 NULL，不用包头长度猜测应用流量。

**已知边界**：授权仍是目的 IP+端口，不是 SNI/HTTP Host/证书身份校验；已观察的黑名单别名可压过共享 IP 上的 allow，但无法推断未查询的别名。无 TLS 正文或 URL 路径；UDP 同五元组 socket 重开不能等价识别为新连接；有界观察不等于任意吞吐下永不丢包。发生观察缺失必须失败，不能宣称 100% 成功。Docker Desktop Linux VM 的 18 项专项结果与原生 GitHub 整栈/负载结果分开。

**未采用**：仅 DNS 无法阻止直连 IP；依赖用户 HTTP_PROXY 可被忽略；TLS 解密超出范围；全禁网不满足装包/接口访问目标。

## R-03 工具目录的单一事实源与授权注入

**Decision**: 新建 `backend/app/executors/catalog.py` 作为**唯一事实源**：每个工具声明 `name / display_name / description / kind(builtin|sandbox) / param_schema / timeout_seconds`。后端 schema 校验、`GET /tools` 接口、Worker 注入、前端工具选择全部读它；`tools` 表由 seed 迁移写入同一份清单，仅承载「是否启用」与展示文案的可运营部分。

执行侧在 `app/tasks/execute_run.py` 取「不可变初始 Run 快照 ∩ 当前 Agent 勾选 ∩ 代码目录中显式启用的 DB 工具」，空集即无工具；每次工具派发前再读取当前 DB 标量，避免长寿命 ORM 缓存掩盖撤权。`0004_tool_grant_history` 在禁用/删除时原子修剪 Agent 授权，并禁止已知 Run 快照改写；旧历史保持 SQL NULL，不按今日配置回填。

**Rationale**: 当前三处不一致（前端常量、后端 `ALLOWED_TOOLS`、真实执行器）根因是「清单有三份」。收敛到一份 + 一张派生表，是唯一能防止再次漂移的做法。`GET /tools` 供前端拉取，使前端不再持有硬编码清单。

**Alternatives considered**:
- 只改两处常量对齐：治标，下次加工具仍会漂移。
- 只放 DB 表、代码读表：多一次查询且迁移期启动顺序耦合；代码常量 + seed 表更稳。

## R-04 执行记录落在哪张表

**Decision**: 新增 `sandbox_executions` 表（与 `steps` 1:1，`step_id` 唯一），不把执行明细塞进 `steps.output`。

**Rationale**: `steps.output` 是 jsonb，塞入 1MB 的 stdout/stderr 会同时污染三个地方：运行详情接口的响应体（现在一次返回全部 steps）、WS 推送（`step_update` 带完整 output）、以及配额统计（要按 Agent/时间聚合执行次数与时长，jsonb 无法建有效索引）。独立表能用索引做配额与统计查询，且大文本字段按需读取。

**Alternatives considered**:
- 复用 `steps` 加列：`steps` 是所有工具共用的表，为一种工具加 8 个专用列会污染模型。
- 独立表 + 冗余 `steps` 记录：保留 `steps`（供 WS 与步骤列表展示），明细单独取，二者通过 `step_id` 关联。

## R-05 制品回收方式

**Decision**: 执行结束后用 Docker SDK `container.get_archive('/workspace/output')` 流式读取 tar，边解边逐文件校验（数量、单文件大小、总量），达标文件直接 `put_object` 到 MinIO，键为 `artifacts/{run_id}/{execution_id}/{filename}`，同时算 `sha256` 落库。

**Rationale**: 只回收约定的 `output/` 目录，避免把整个工作目录（含临时文件、依赖缓存）拖回来。流式读取避免大文件落宿主临时盘（宪法「资源上限」与磁盘配额约束）。sha256 支撑 SC-007（下载内容与执行时一致）。

**Alternatives considered**:
- 挂载宿主卷让脚本直接写到共享目录：脚本可写宿主路径，违背 FR-008（不得访问宿主文件系统）。
- 预签名 PUT 直传：脚本在容器内无法安全持有凭证，且沙箱默认出网。

## R-06 超时、取消与并发排队

**Decision**: 三层控制。
- 超时：`container.wait(timeout=limit)`；`ReadTimeout` 即 `kill(SIGKILL)` + `remove(force=True)`，标记 `status=timeout`。
- 取消：沿用既有 `should_abort` 回调（`execute_run.py` 第 336 行的 `check_cancelled`）。沙箱执行器接收该回调，在等待期间以 1s 为步长轮询，命中即 `kill`。这意味着容器等待不能是一次性阻塞 120s，需分段等待。
- 并发：Redis 信号量 `sbx:sem:{n}`（`SBX_MAX_CONCURRENCY`，默认 4）。拿不到信号量 → Celery `self.retry(countdown=退避)`，最多 3 次（累计约 45s），仍失败则把该步标为 `failed` 且错误分类 `resource`，并在可读信息里说明「沙箱繁忙」。

**Rationale**: 现有取消是「Worker 查 DB 状态」的轮询式，沙箱必须复用同一语义，否则取消一个正在跑死循环的 Run 会拖到超时才停（FR-014 要求立即终止）。并发用 Redis 信号量而非 Worker 进程内计数，是因为 Worker 可多副本、`--concurrency` 也可调。

**Alternatives considered**:
- 进程内 `asyncio.Semaphore`：多副本下总量失控。
- Celery 队列路由到专用 `sandbox` 队列（架构文档提到 `worker-sandbox`）：更彻底的隔离，但本特性 v1 落单队列 + 信号量即可；队列拆分留到压测（P3）后按实测决定。

## R-07 脚本来源与注入面

**Decision**: 脚本内容由 LLM 通过 `run_python`/`run_shell` 工具参数提交，**只作为容器内文件内容**写入（`put_archive` 写入 `/workspace/main.py` 或 `/workspace/run.sh`），绝不拼接进任何 shell 命令行；`run_shell` 的命令串在执行前一并计入审计（截断 4KB）。

**Rationale**: 参数透传是沙箱类功能的典型注入口。用 `put_archive` 写文件 + 固定 `command=["python","/workspace/main.py"]`，容器启动命令是常量，用户内容永远是数据不是代码路径。这也让 `code_sha256` 与审计可复现。

**Alternatives considered**:
- `docker exec sh -c "{cmd}"`：直接构成命令注入。
- Base64 塞进 `command`：参数长度受限（>128KB 会失败），且仍拼在命令行。

## R-08 沙箱镜像与依赖策略

**Decision**: 提供 `deploy/sandbox/Dockerfile` 构建 `aw-sandbox-python:3.12`，预装标准库 + `numpy/pandas/matplotlib/requests/beautifulsoup4/openpyxl`；非 root 用户 `sandbox`（UID 10001）；工作目录 `/workspace`（`/workspace/output` 为产物出口）；镜像以固定 tag 引用并在 compose 中随部署构建。允许运行期 `pip install`（默认放行下可用），但**不跨执行缓存**——`--rm` 语义下每次从干净层开始。

**Rationale**: 预装高频库避免「每次执行都花 30s 装 pandas」这种把时长配额吃光的体验；不做跨执行缓存是因为可写层持久化会引入「上一次执行留下的恶意包/后门」的横向污染，与「一次性」语义冲突。

**Alternatives considered**:
- 每执行 `pip install` 全部依赖：慢且依赖公网可用性。
- 持久化 `~/.cache/pip` 卷：提速明显但引入跨执行状态（spec 明确「不保留跨调用状态」）。

## R-09 前端改造面

**Decision**: 四处改动。
1. Agent 表单：工具选项由 `GET /tools` 拉取（删除 `components/agent/constants.ts` 硬编码），未选工具时的提示文案改为与后端一致的真实语义。
2. Agent 表单新增「出网」区块：`默认（放行并记录）/ 限制模式`，限制模式下维护域名列表（调 `egress-rules` 接口）。
3. 运行详情新增「代码执行」卡片：代码折叠（默认收起）、`stdout`/`stderr` 分栏、退出码/耗时/资源徽标、失败分类标签、本次出站访问列表、产出文件下载。
4. 管理后台新增两张表：出站黑名单维护、出站访问记录查询（按 Agent/时间/结果筛选）。

**Rationale**: 全部落在既有页面结构内（`RunDetail`/`StepList`/`AgentForm`/`AdminPage`），不新增路由；符合 FR-028「不引入第二套执行视图」。

**Alternatives considered**:
- 独立的「沙箱控制台」页面：与运行详情割裂，用户要在两个地方对照代码与结果。

## R-10 存量工具勾选的迁移

**Decision**: 一次数据迁移（Alembic data migration），按映射改写 `agents.tools`：

| 旧名 | 新名 | 说明 |
|---|---|---|
| `web_search` | `web_search` | 不变 |
| `web_fetch` | `read_url` | 语义一致（抓取网页） |
| `shell` | `run_shell` | 语义一致（沙箱执行命令） |
| `code_interpreter` | `run_python` | 语义一致（沙箱执行代码） |
| `http` | 移除 | v1 无通用 HTTP 工具；需要时用 `run_python` 调库 |

迁移时输出受影响 Agent 数量到迁移日志；历史 `runs.result`/`steps` 中的旧工具名**不改写**（历史快照不可追溯修改，FR-005）。

**Rationale**: 用户当前勾选的多是「从未实现过」的工具，若直接清空会让人困惑；能映射的保守映射，不能映射的明确移除并留日志。

**Alternatives considered**:
- 保留旧名做别名（运行时把 `web_fetch` 当 `read_url`）：目录里长期存在两个同义名，正是本次要消灭的问题。
