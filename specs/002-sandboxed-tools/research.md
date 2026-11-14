# Research: 沙箱代码执行与工具授权（Sandboxed Tools）

**Phase 0 产出**：技术决策论证。spec 无 NEEDS CLARIFICATION，本文档论证 10 项关键实现路径，其中 R-02（出站可见性）是 spec 阶段标记的最高风险项，在此定案并记录已知缺口。

## R-01 沙箱的隔离与生命周期由谁管理

**Decision**: 复用宿主 Docker 守护进程，Worker 通过 Docker SDK（`docker` Python 包）以「一次性容器」方式创建/执行/销毁沙箱，Worker 容器挂载 `/var/run/docker.sock`。

**Rationale**: 宪法已定「Docker 一次性容器」为沙箱形态，不引入第二套隔离设施。SDK 相对 CLI 的三个实质优势：① 不存在 shell 字符串拼接，用户脚本走 `environment`/`command` 或文件写入，杜绝参数注入；② 资源限制（`nano_cpus`/`mem_limit`/`pids_limit`/`read_only`+`tmpfs`）以结构化参数下发，不依赖易错的 CLI 参数拼装；③ `wait(timeout=)`、`kill(signal='SIGKILL')`、`logs()`、`get_archive()` 覆盖超时终止、输出采集、产物回收全链路，且是流式接口，不需要落临时文件。

一次性语义：每次执行 `networks.create()` 独立 bridge 网络 → `containers.run(detach=True, remove=False)` → `wait()` → 采集 → `remove(force=True)` + `networks.remove()`。`remove=True` 不用于 run，因为需要在容器退出后仍能 `get_archive()` 取产物，销毁放在 finally。

**Alternatives considered**:
- Docker-in-Docker（`--privileged` + 内部 dockerd）：隔离更强，但 Worker 需特权模式，且镜像层重复拉取，单机内存开销翻倍；宪法的「资源上限」约束下不划算。
- `subprocess` 调 `docker run`：需要拼参数与解析 stdout，注入面与脆弱性都更高；`get_archive` 类流式能力缺失。
- gVisor / Kata：宪法明确留到 P3 加固，本特性不做，但 `sandbox` 执行器的镜像与参数是集中配置的，后续替换运行时只需改一处。

**风险与缓解**：挂载 socket 等价于给 Worker 宿主 root 权限。缓解：① 沙箱容器参数由服务端代码固定，**不接受**任何来自用户/LLM 的 docker 参数；② 镜像白名单（`SBX_ALLOWED_IMAGES`）硬编码在配置，运行时校验；③ 网络固定 `bridge` + 独立自定义网络，不允许 `host`/`none` 之外的自选模式；④ 该风险在 P3 安全复核中作为已知项记录。

## R-02 出站可见性与黑名单强制（spec 标记的最高风险）

**Decision**: 三层组合，**拦截用内核规则、记录用 DNS + 连接跟踪**，不做 TLS 解密：

1. **拦截层**：每个执行一个独立 bridge 网络。执行前用一个**系统短命 init 容器**（`--net=host --cap-add=NET_ADMIN`，固定镜像，不接收用户输入）向该网络的 `FORWARD` 链插入规则：`DROP` 发往 RFC1918 网段、`169.254.0.0/16`（云元数据）、`127.0.0.0/8` 与内部服务网段；先 `ACCEPT` 到 DNS 代理的 53 端口，其余放行。规则按网络名打标签（`aw-sbx-{exec_id}`），执行结束一并删除。
2. **域名层**：容器 `dns` 指向受控 DNS 代理（compose 内新增 `dns-proxy` 服务）。它记录每次查询的 `(domain, 时刻, 来源执行)`；黑名单域名在此直接返回 `NXDOMAIN`（第二道闸，比 iptables 更快给出可读失败）。同时 `iptables` 把「53 端口只允许到 dns-proxy」固定住，容器无法换用外部 DNS 绕过记录。
3. **记录层**：执行结束时用同一个 init 容器镜像 `conntrack -L` 快照该容器 IP 的全部连接，得到真实目标 `IP:port` 与流量字节数；用第 2 层的 DNS 查询表在该时间窗内把 IP 反查为域名。

**Rationale**: spec 的 SC-009 要求「直连 IP、长连接、非常规端口都算数」，SC-010 要求黑名单 100% 拦截。纯 DNS 方案挡不住直连 IP；纯代理方案需要注入 CA 才能看 HTTPS 目标，且容器内进程可无视 `HTTP_PROXY` 环境变量直连。内核 DROP 不依赖容器内任何配合，是唯一能同时满足两条 SC 的做法；DNS 负责给 IP 补上可读域名，conntrack 负责覆盖「绕过 DNS 的那部分」。

**Known gaps（写进 quickstart 与运维文档，不隐藏）**：
- 不做 TLS 解密 → 只能到「目标域名/IP + 端口」粒度，**看不到具体 URL 路径与请求体**。
- 域名反查依赖 DNS 查询表：CDN 共享 IP 或 IP 直连场景下域名会缺失，此时记录为 `ip-direct`（该行为本身可疑，审计时天然是红旗）。
- `conntrack` 快照在长连接跨执行存活时有遗漏可能；对 `TIME_WAIT` 条目仍可读，实测覆盖足够。

**Alternatives considered**:
- 透明代理（mitmproxy/squid）按 SNI 转发：能拿域名但拿不到 IP 直连目标，且需劫持 443，运维复杂度高。
- 仅记录 DNS：实现最省事，但违背 SC-009 的「直连 IP 也算」。
- 全禁网：spec 修订时已被用户否决（装包与调接口不可用）。

## R-03 工具目录的单一事实源与授权注入

**Decision**: 新建 `backend/app/executors/catalog.py` 作为**唯一事实源**：每个工具声明 `name / display_name / description / kind(builtin|sandbox) / param_schema / timeout_seconds`。后端 schema 校验、`GET /tools` 接口、Worker 注入、前端工具选择全部读它；`tools` 表由 seed 迁移写入同一份清单，仅承载「是否启用」与展示文案的可运营部分。

执行侧改造点（`app/tasks/execute_run.py` 第 88、97 行）：把无条件 `executors=default_executors()` 改为 `executors=build_executors(agent.tools)`——按 Agent 勾选项从注册表取执行器，空集则传空字典。

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
