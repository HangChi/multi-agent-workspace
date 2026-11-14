# Tasks: 沙箱代码执行与工具授权（Sandboxed Tools）

**Input**: Design documents from `/specs/002-sandboxed-tools/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: 宪法强制 SDD+TDD（NON-NEGOTIABLE）。每个用户故事的测试任务**必须先写、先红（FAIL）**，再实现使其变绿。本特性有两处必须反向验证的测试：**越权矩阵**与**黑名单求值**——它们最容易写成一跑就绿的假测试（001 的经验：并发锁测试反向验证过才发现假绿）。

**Organization**: 按用户故事分组，每个故事可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 所属用户故事（US1~US5）
- 描述中必须含精确文件路径

## Path Conventions

- Web app: `backend/`（FastAPI）、`frontend/`（React SPA）、`deploy/`（Compose + 沙箱镜像 + verify 脚本）
- 提交规范（宪法）：每个 Phase 一次提交 `feat(<域>): <内容>（T0xx-T0yy）`；每个 Phase 结束后勾选 tasks.md 并提交 `docs(tasks): 更新 002-sandboxed-tools 任务状态`

---

## Phase 1: Setup（沙箱设施与配置）

**Purpose**: 让「能起一个隔离容器」这件事先成立，后续所有故事都依赖它

- [x] T001 后端新增依赖：backend/pyproject.toml 加 `docker>=7.1`（Docker SDK）；确认 `uv sync` 后 `import docker` 可用，不引入其他容器相关重依赖
- [x] T002 [P] 配置项：backend/app/core/config.py 增 `SBX_IMAGE/SBX_TIMEOUT_DEFAULT/SBX_TIMEOUT_MAX/SBX_CPU/SBX_MEM_MB/SBX_DISK_MB/SBX_PIDS/SBX_MAX_CONCURRENCY/SBX_STDOUT_MAX_BYTES/SBX_CODE_MAX_BYTES/SBX_ARTIFACT_MAX_FILES/SBX_ARTIFACT_MAX_FILE_MB/SBX_ARTIFACT_MAX_TOTAL_MB`、`EGRESS_DNS_PROXY/EGRESS_BUILTIN_BLACKLIST`、`TOOL_DENY_THRESHOLD`，并在 `deploy/.env.example` 同步（T007 一起提交）
- [x] T003 [P] 沙箱镜像：deploy/sandbox/Dockerfile（`python:3.12-slim` + numpy/pandas/matplotlib/requests/beautifulsoup4/openpyxl，非 root 用户 `sandbox` UID 10001，`WORKDIR /workspace` 并预建可写 `output/`，无 ENTRYPOINT）+ deploy/sandbox/README.md（镜像用途与重建命令）
- [x] T004 [P] 网络 init 镜像：deploy/sbx-net/Dockerfile（alpine + iptables + conntrack-tools，`ENTRYPOINT []`，只被以 `--net=host --cap-add=NET_ADMIN` 短命调用）
- [x] T005 [P] DNS 代理：deploy/dns-proxy/（Dockerfile + dnsmasq.conf，开启 `log-queries` 与 `log-facility=-`（stdout），黑名单域名走 `address=/<domain>/` 返回 NXDOMAIN，配置由 `/etc/hosts.d` 类挂载注入）
  - 实现修正：① 必须用 `log-queries=extra`——普通 `log-queries` 只记 forwarded/reply，**不带客户端 IP**，SC-009 的"谁访问了什么"就断了；② 黑名单改为**静态只读挂载** `blacklist.conf`（默认全注释模板），不做运行期按执行重写：dns-proxy 是全局共享服务，并发执行会互相覆盖；Agent 级限制模式下沉到每次执行的 iptables 规则；③ 无宿主端口，只被 Worker 接入每次执行的独立网络。
- [x] T006 部署接线：deploy/docker-compose.yml 增 `dns-proxy` 服务（内部网络、无外网暴露）+ `worker` 挂载 `/var/run/docker.sock` 与 `SBX_*` 环境变量 + 各服务的 `shm_size`/资源上限校验；确保 worker 仍能连 postgres/redis（不要给 worker 加 `network_mode: host`）
  - 实现修正：worker 必须加 `group_add: ['${DOCKER_SOCK_GID:-0}']`。worker 以非 root(10001) 运行，而 socket 是 `root:<组> 0660`，只挂载不加组会 `PermissionError: [Errno 13]`（Checkpoint 实测踩到）。只加补充组、不改 `USER`，进程身份仍是 10001。
- [x] T007 [P] 环境模板：deploy/.env.example 补 T002 的全部变量（含注释说明默认值与上限），不写真实值

**Checkpoint**: `docker build` 两个镜像成功；compose 起来后 `docker exec aw-worker docker version` 有输出；`dns-proxy` 可解析公网域名

**Phase 1 完成记录（2026-11-14）**：
- 三个镜像（sandbox / sbx-net / dns-proxy）build 成功，自检通过（sandbox 无 ENTRYPOINT 时 CMD 兜底 exit 2）。
- Checkpoint 第 1 项**改写**为 `docker exec aw-worker python -c "import docker; docker.from_env().version()"`：worker 镜像**故意不装 docker CLI**（走 Python SDK，CLI 无用且增加攻击面），原命令必然 `executable file not found`。
- 实测：worker 经 SDK 连通守护进程（v29.9.3 / API 1.56），可见三个沙箱相关镜像。
- 端到端实测：起独立网络 `aw-sbx-*` → 接入 dns-proxy（IP 172.30.0.2）→ 起沙箱容器（user 10001:10001, mem 256m, nano_cpus 0.5, pids 64, `dns=[172.30.0.2]`）→ example.com / pypi.org 均解析成功 → 退出码 0 → 清理网络。
- dns-proxy 日志确认记录 `query[A] pypi.org from 172.30.0.3`，SC-009 域名审计链路打通。
- 质量门禁：`ruff check` 干净、`mypy app` 68 文件 0 问题、`pytest` 315 passed。
- 提交 `af6f4c7`。
- ⚠️ 未验证项：iptables / conntrack（本机 macOS Docker Desktop，无内核 netfilter），须在 Linux 云主机验 S5。

---

## Phase 2: Foundational（阻塞性基础，必须先于所有用户故事）

**Purpose**: 数据层、工具目录单一事实源、容器生命周期封装、出站策略求值

**⚠️ CRITICAL**: 本 Phase 完成前不要开始任何用户故事

- [x] T008 新增模型：backend/app/models/tool.py、sandbox_execution.py、egress.py（egress_rules + egress_accesses），严格对齐 data-model.md 的字段/类型/约束/索引/唯一约束（含 `sandbox_executions.step_id` UNIQUE、`egress_rules` 的 6 元组唯一）
- [x] T009 既有模型变更：backend/app/models/agent.py 增 `egress_mode`（enum default/restricted）；backend/app/models/artifact.py 增 `sha256`（char(64), nullable）；同步 backend/app/models/__init__.py 导出
- [x] T010 迁移：backend/alembic/versions/0003_sandbox_tools.py（建 4 表 + 加 2 列 + `tools` 表 seed 5 条 + 存量 `agents.tools` 改名映射：`web_fetch→read_url`、`shell→run_shell`、`code_interpreter→run_python`、`http` 移除；迁移日志输出受影响 Agent 数）。**注意**：迁移用 `op.execute` 写数据，反向迁移可回滚列但不回滚数据改名
- [x] T011 [P] 工具目录单一事实源：backend/app/executors/catalog.py（`ToolSpec` dataclass：name/display_name/description/kind/param_schema/timeout_seconds；`CATALOG` 常量声明 5 个工具；`get_tool(name)`、`enabled_tools()`）
- [x] T012 [P] 执行器基座扩展：backend/app/executors/base.py 增 `ExecutorRegistry.from_names(names, catalog)`（缺工具的 name 静默跳过并记 warning）与 `DeniedToolResult` 类型（对齐 contracts/api.md 的 `{"ok":false,"denied":true,...}`）
- [x] T013 [P] DTO：backend/app/schemas/tool.py（ToolOut 列表）、sandbox.py（ExecutionSummary/ExecutionDetail/EgressAccessOut）、egress.py（EgressRuleIn/Out、BlacklistIn/Out）
- [x] T014 [P] 制品服务扩展：backend/app/services/artifact_service.py 增 `put_bytes(run_id, execution_id, filename, data, mime=None) -> (object_key, sha256)`（服务端直传 + 流式 sha256 + 键前缀 `artifacts/{run_id}/{execution_id}/`）
- [x] T015 容器生命周期封装：backend/app/executors/sandbox_client.py（`SandboxRun`：创建独立 bridge 网络 `aw-sbx-{exec_id}` → `containers.run(detach=True, remove=False)` → 分段 `wait(timeout=1s)` 循环 + `abort_check` 回调 → 超时/取消 `kill(SIGKILL)` → `logs()` → `get_archive()` → finally `remove(force=True)` + `networks.remove()`；参数全部来自配置，**不接受任何调用方传入的 docker 参数**）
- [x] T016 [P] 出站策略求值：backend/app/executors/egress.py（纯函数 `evaluate(target_ip, target_domain, port, *, mode, global_rules, agent_rules) -> Decision(action, matched_rule_id)`；顺序：内置黑名单 → 全局 deny → (restricted) agent allow → default allow；不含任何 IO，便于单测）
- [x] T017 测试基座扩展：backend/tests/conftest.py 增 `fake_docker` fixture（mock SDK：记录创建参数、可控 wait 超时/退出码/归档内容）、`sbx_semaphore` fixture（真实 Redis，测完清理）、`sandbox_factory`（造 sandbox_executions 记录）

**Checkpoint**: 迁移可执行（含 seed 与改名）；`evaluate()` 与 `from_names()` 可单测；fake_docker 能驱动 sandbox_client 全流程

---

## Phase 3: User Story 1 - 在隔离环境中执行脚本（Priority: P1）🎯 MVP

**Goal**: Agent 能在一次性容器内跑 Python/shell，拿回 stdout/stderr/退出码，超时与取消能立刻杀容器

**Independent Test**: 只勾 `run_python` 的 Agent 完成「计算 1~100 平方和」，运行详情可见脚本、输出、退出码、耗时（quickstart S1）

### Tests for User Story 1（先写，先红后绿）⚠️

- [ ] T018 [P] [US1] 限额与分类单元测试 backend/tests/unit/test_sandbox_limits.py：退出码 0→`ok`、非零→`failed(exit_code)`、wait 超时→`timeout`、OOM→`resource(oom)`、pids 超限→`resource(pids)`、磁盘超限→`resource(disk)`、守护不可达→`infra`；每类断言 `error_message` 中文可读且非「未知错误」
- [x] T019 [P] [US1] 生命周期契约测试 backend/tests/unit/test_sandbox_client.py（fake_docker）：断言网络以独立名字创建、容器参数含 `nano_cpus/mem_limit/pids_limit/read_only/tmpfs`、超时后调 `kill(SIGKILL)`、`finally` 必调 `remove(force=True)` 与 `networks.remove()`、异常路径也不残留
- [ ] T020 [P] [US1] 执行器单元测试 backend/tests/unit/test_sandbox_executor.py：`run_python`/`run_shell` 的 schema 形状；参数缺省用默认 timeout；**失败必须返回 `{"ok": false, "status": ..., "exit_code": ..., "stdout": ..., "stderr": ...}` 而非抛异常**（FR-013；反向验证：把实现改成抛异常，此测试必须红）
- [ ] T021 [P] [US1] 输出截断单元测试 backend/tests/unit/test_sandbox_output.py：输出 3MB → 保留尾部 ≤1MB、`stdout_truncated=true`、`stdout_full_bytes=3145728`；二进制/乱码输出不抛异常、降级为可读替换

### Implementation for User Story 1

- [ ] T022 [US1] 沙箱执行器：backend/app/executors/sandbox.py（`RunPythonExecutor` / `RunShellExecutor`，实现 ToolExecutor 协议，schema 取自 catalog）
- [ ] T023 [US1] 脚本注入方式：脚本经 `put_archive` 写入 `/workspace/main.py` 或 `/workspace/run.sh`，容器 `command` 为常量（`["python","-u","/workspace/main.py"]` / `["bash","/workspace/run.sh"]`）；`run_shell` 的命令串额外记入审计摘要（截断 4KB）。**禁止**把用户内容拼进任何命令行（R-07） （backend/app/executors/sandbox.py）
- [ ] T024 [US1] 资源限额映射：`nano_cpus`/`mem_limit`/`pids_limit`/`read_only=True` + `tmpfs` 写盘配额/`security_opt` 基础项；`limits` 快照写入 DB （backend/app/executors/sandbox_client.py）
- [ ] T025 [US1] 超时与取消：分段等待（1s）轮询 `abort_check`（接既有 `should_abort`）；命中即 `kill` 并置 `status=failed/error_category=cancelled`；`timeout_s` 夹取在 `[1, SBX_TIMEOUT_MAX]` 且不超过所属 Run 剩余预算 （backend/app/executors/sandbox_client.py + backend/app/executors/sandbox.py）
- [ ] T026 [US1] 执行服务：backend/app/services/sandbox_service.py（`start_execution()` 落 running 记录 → `finish_execution()` 落终态/耗时/退出码/stdout/stderr；失败分类映射表集中在此）
- [ ] T027 [US1] Worker 接线：backend/app/tasks/execute_run.py 把 `executors=default_executors()` 改为 `build_executors(agent.tools)`（build_executors 读 catalog + 注册沙箱执行器），并把 `should_abort` 传进执行器上下文
- [ ] T028 [US1] 事件：backend/app/tasks/events.py 增 `sandbox_started/sandbox_output/sandbox_finished`；backend/app/api/v1/ws.py 透传（`sandbox_output` 增量 ≤4KB/条）
- [ ] T029 [US1] 前端执行卡片：frontend/src/components/run/ExecutionCard.tsx（代码折叠默认收起、stdout/stderr 分栏、退出码/耗时/资源徽标、失败分类标签）+ frontend/src/pages/RunDetailPage.tsx 接入 + frontend/src/hooks/useRunSocket.ts 处理 3 类新事件
- [ ] T030 [US1] 审计挂接：沙箱执行结束时写 `audit_logs(action='sandbox_exec')`（含 language/status/exit_code/duration 摘要，不含脚本全文） （backend/app/services/sandbox_service.py）
- [ ] T031 [US1] US1 闭环集成测试 backend/tests/integration/test_sandbox_flow.py：mock LLM 发 `run_python` tool_call → fake_docker 执行 → step output 含 stdout → 运行 completed（SC-001 的时序断言用 fake 时钟）

**Checkpoint**: US1 独立可用——「Agent 能跑脚本并看到结果」

---

## Phase 4: User Story 2 - 勾选的工具才真的可用（Priority: P1）

**Goal**: 工具目录唯一、按 Agent 勾选注入、越权调用被拒并留痕

**Independent Test**: 只勾 `calculate` 的 Agent 无法调 `run_shell`，越权时收 `denied` 结构 + 审计一条（quickstart S3）

### Tests for User Story 2（先写，先红后绿）⚠️

- [ ] T032 [P] [US2] 目录一致性测试 backend/tests/unit/test_catalog.py：5 个工具齐全；**每个工具名在运行时都能解析到一个真实执行器**（反向验证：删掉一个执行器注册，此测试必须红）；`enabled=false` 的工具不进 `enabled_tools()`
- [ ] T033 [P] [US2] 授权注入测试 backend/tests/unit/test_tool_grant.py：勾 A → registry 只有 A；`tools=[]` → registry 为空且 loop 不产生任何 tool 调用；勾了目录里已下线工具 → 该工具被跳过且记 warning
- [ ] T034 [P] [US2] 越权拒绝测试 backend/tests/unit/test_tool_denied.py：模型调未授权工具 → step output 为 `denied` 结构且 `message` 列出可用工具；写 `audit_logs(action='tool_denied')`；同一 run 连续越权达 `TOOL_DENY_THRESHOLD` → 该步中止（反向验证：去掉阈值判断，测试必须红）
- [ ] T035 [P] [US2] 目录 API 集成测试 backend/tests/integration/test_tools_api.py：`GET /tools` 字段与 catalog 一致；`POST/PATCH /agents` 传非法工具名 → 400 且 `detail` 含可选清单；传已下线工具同样 400

### Implementation for User Story 2

- [ ] T036 [US2] 目录端点：backend/app/api/v1/tools.py（`GET /tools`，登录可读，只返回启用项）+ backend/app/main.py 注册路由
- [ ] T037 [US2] Agent 校验改造：backend/app/schemas/agent.py 的 `ALLOWED_TOOLS` 硬编码删除，改读 catalog 的启用集合；错误信息带可选清单
- [ ] T038 [US2] 运行时注入：backend/app/orchestrator/runner.py 的 `self.executors` 改为由 `build_executors(agent.tools)` 提供；`executor is None` 分支改为「未授权」语义（不再叫「未知工具」），生成 `denied` 结果并推 `tool_denied` 事件
- [ ] T039 [US2] 越权审计：`tool_denied` 的 `write_audit` 挂接（含 tool_name、run、agent、可用工具列表摘要） （backend/app/orchestrator/runner.py）
- [ ] T040 [US2] 前端工具来源：frontend/src/components/agent/AgentForm.tsx 改从 `GET /tools` 渲染；删除 frontend/src/components/agent/constants.ts 的硬编码清单（`TOOL_HINTS` 改用 `display_name`/`description`）；空集提示文案与后端语义对齐
- [ ] T041 [US2] 迁移验证：backend/tests/integration/test_tool_migration.py 断言迁移后无旧名残留、可映射项已改名、历史 `steps.tool_name` 未变（FR-005）

**Checkpoint**: US1+US2 = 「能力有了，且按 Agent 粒度开关」——本轮缺陷修复完成

---

## Phase 5: User Story 3 - 脚本产出文件成为可下载制品（Priority: P2）

**Goal**: `output/` 目录下的文件自动成为运行制品，可下载且内容一致

**Independent Test**: 生成 20 行 CSV，详情页可下载，sha256 与执行时一致（quickstart S4）

### Tests for User Story 3（先写，先红后绿）⚠️

- [ ] T042 [P] [US3] 回收与上限单元测试 backend/tests/unit/test_artifact_collect.py（fake_docker 的 tar 流）：只收 `output/` 内文件；60 个文件 → 只留 50 且标记截断；单文件 30MB → 拒绝并提示；总量超 50MB → 截断；**同名文件不覆盖**（追加序号）；sha256 与内容一致
- [ ] T043 [P] [US3] 制品归属集成测试 backend/tests/integration/test_artifact_access.py：owner 可下载（302）；他人 403；制品列表出现在 `GET /runs/{id}` 且含 `sha256`

### Implementation for User Story 3

- [ ] T044 [US3] 制品回收：backend/app/services/sandbox_service.py 接 `get_archive('/workspace/output')` 的流式 tar 解析（边解边判上限），逐文件走 `artifact_service.put_bytes`
- [ ] T045 [US3] 制品种子与命名：`artifacts` 落库（run_id/step_id/filename/size/mime/sha256/object_key），同名冲突追加 `-2`/`-3`；单次执行超限时写 `audit_logs(action='artifact_truncated')` （backend/app/services/sandbox_service.py）
- [ ] T046 [US3] 前端制品区：frontend/src/components/run/ExecutionCard.tsx 内展示产物列表（文件名/大小/下载），复用既有下载链
- [ ] T047 [US3] 二进制与编码兜底：非文本产物不做编码转换、mime 按扩展名推断，未知类型 `application/octet-stream` （backend/app/services/sandbox_service.py）

**Checkpoint**: 脚本能交付真实工作物（csv/图/报告）

---

## Phase 6: User Story 4 - 执行可观测、可追责、受配额约束（Priority: P2）

**Goal**: 每次执行留下结构化记录、失败分类可读、并发受控

**Independent Test**: 连续跑含死循环与内存暴涨的执行，记录字段齐全、分类正确、超并发时排队而非报错（quickstart S6）

### Tests for User Story 4（先写，先红后绿）⚠️

- [ ] T048 [P] [US4] 指标落库测试 backend/tests/unit/test_execution_metrics.py：`duration_ms/cpu_ms/mem_peak_bytes/exit_code/error_category` 五类失败均有值；cgroup 读不到时字段为 NULL 而非报错
- [ ] T049 [P] [US4] 并发配额测试 backend/tests/unit/test_sandbox_quota.py（真实 Redis）：信号量满 → `retry(countdown)` 且最多 3 次；3 次后 `status=failed/error_category=resource` 且 `error_message` 含「沙箱繁忙」；信号量在异常路径必须释放（反向验证：删掉 finally 释放，测试必须红）
- [ ] T050 [P] [US4] 详情与统计集成测试 backend/tests/integration/test_execution_detail.py：`GET /runs/{id}/executions/{eid}` 字段完整；`GET /runs/{id}` 的 `executions` 摘要不含 code/stdout（体积约束）；越权 403

### Implementation for User Story 4

- [ ] T051 [US4] 资源峰值采集：从容器 `stats(stream=False)` 取 `memory_stats.peak`/`cpu_stats.cpu_usage.total_usage`，在 `kill` 前采样一次；异常吞掉并置 NULL （backend/app/executors/sandbox_client.py）
- [ ] T052 [US4] 失败分类与文案：backend/app/services/sandbox_service.py 增 `ERROR_MESSAGES` 映射（timeout/oom/pids/disk/policy/infra/exit_code/cancelled 各一条中文可读原因，含「怎么办」的提示）
- [ ] T053 [US4] 并发信号量：backend/app/services/sandbox_service.py 用 Redis `INCR/DECR` + TTL 自愈（防进程崩溃不释放）实现计数；Celery 侧 `self.retry(countdown=backoff)` 退避 5/15/25s
- [ ] T054 [US4] 执行详情端点：backend/app/api/v1/runs.py 增 `GET /runs/{id}/executions/{eid}`（含 `artifacts`），`GET /runs/{id}` 增 `executions` 摘要（不含大字段）
- [ ] T055 [US4] 统计扩展：backend/app/api/v1/stats.py 增 `sandbox_runs_today` / `sandbox_minutes_today`（复用 7 天窗口口径）
- [ ] T056 [US4] 前端详情：ExecutionCard 展示资源峰值与失败分类徽标；「沙箱繁忙」用既有 Toast/Badge 呈现 （frontend/src/components/run/ExecutionCard.tsx）

**Checkpoint**: 出问题能定位到「哪次执行、什么原因」，并发不拖垮全站

---

## Phase 7: User Story 5 - 出网可用、可控、可追溯（Priority: P2）

**Goal**: 默认放行并全量记录、全局黑名单强制、Agent 级限制模式

**Independent Test**: 默认能 `pip install`；访问 `169.254.169.254` 被拦且留痕；限制模式下只放行白名单域名（quickstart S5）

### Tests for User Story 5（先写，先红后绿）⚠️

- [ ] T057 [P] [US5] 策略求值单元测试 backend/tests/unit/test_egress_eval.py：内置黑名单命中→deny；全局 deny 优先于 agent allow（同一目标同时命中时）；`restricted` 无规则→全 deny；`default` 无规则→allow；cidr/域名通配/端口区间各自命中；`ip-direct` 未命中任何规则→allow 但记 `ip-direct`
- [ ] T058 [P] [US5] 记录归并单元测试 backend/tests/unit/test_egress_record.py：DNS 查询表 + conntrack 快照 → 归并出 `target_domain`；同 IP 多域名按时间窗取最近；反查失败标 `ip-direct`；批量写入条数与快照条数一致
- [ ] T059 [P] [US5] 出站 API 集成测试 backend/tests/integration/test_egress_api.py：agent rules CRUD（owner/admin、重复 409、被黑名单覆盖 400）；admin 黑名单 CRUD（内置项删除 400、成员调用 403）；`GET /admin/egress-accesses` 筛选与 `summary` 计数正确

### Implementation for User Story 5

- [ ] T060 [US5] 规则编排：backend/app/executors/egress.py 增 `apply_rules(network_name, rules)`（调用 init 容器执行 iptables 插入/清理，按网络名打标签；`dump_connections(container_ip)` 调 `conntrack -L` 并解析）
- [ ] T061 [US5] DNS 接线：`sandbox_client.py` 给容器传 `dns=[EGRESS_DNS_PROXY]`；iptables 规则里 53 只放行到代理 IP；执行结束从 dns-proxy 拉该执行时间窗的查询记录（stdout 解析或代理侧落 Redis list） （backend/app/executors/sandbox_client.py）
- [ ] T062 [US5] 出站服务：backend/app/services/egress_service.py（规则 CRUD + `snapshot_for_execution()` 求值快照 + `record_accesses()` 批量写入 + `builtin` 项保护）
- [ ] T063 [US5] Agent 出网字段：`agents.egress_mode` 端点接线（执行开始时快照 mode 与 agent 规则，执行中改配置不影响进行中的执行） （backend/app/api/v1/agents.py）
- [ ] T064 [US5] 管理端点：backend/app/api/v1/admin.py 增黑名单 CRUD 与 `GET /admin/egress-accesses`（含 `summary.ip_direct`）
- [ ] T065 [US5] Agent 出站端点：backend/app/api/v1/agent_egress.py（GET/POST/DELETE `/agents/{id}/egress-rules`，校验被黑名单覆盖时 400）
- [ ] T066 [US5] 事件与审计：WS `egress_denied`；`audit_logs(action='egress_denied')`（含 target/port/rule） （backend/app/tasks/events.py + backend/app/services/egress_service.py）
- [ ] T067 [US5] 前端出网配置：frontend/src/components/agent/EgressRules.tsx（模式切换 + 域名清单编辑）+ AgentForm 接入；AdminPage 增黑名单维护与访问记录两张表（含 `ip-direct` 高亮）

**Checkpoint**: 装包调接口可用，同时有黑名单底线与完整留痕

---

## Phase 8: Polish & 跨切面收尾

**Purpose**: 真实栈验证、越权矩阵、数据保留、文档与门禁

- [ ] T068 [P] 真实栈断言（沙箱）：deploy/verify/s3_sandbox_scenarios.py（S1/S2/S3/S4）
- [ ] T069 [P] 真实栈断言（出站）：deploy/verify/s5_egress_scenarios.py（S5：默认放行/元数据拦截/直连 IP/内网拦截/限制模式/内置项保护）
- [ ] T070 [P] 越权矩阵自动化：deploy/verify/sbx_escape_matrix.py（quickstart 的 10 项，逐项断言「失败且不可见」，输出表格化结果）
- [ ] T071 数据保留任务：backend/app/tasks/maintenance.py 增 `purge_sandbox_payloads`（90 天后清空 code/stdout/stderr，保留元数据与指纹）+ beat 每日一次
- [ ] T072 [P] 前端单测：frontend/src/components/agent/__tests__/EgressRules.test.tsx、frontend/src/components/run/__tests__/ExecutionCard.test.tsx、tools 目录渲染用例（vitest）
- [ ] T073 [P] 运维文档：deploy/README.md 增「沙箱设施」章节（镜像重建、dns-proxy 排障、socket 风险声明、**已知缺口：不做 TLS 解密、CDN 共享 IP 可能 ip-direct**）
- [ ] T074 契约同步复核：逐条比对 contracts/api.md 与实现（端点/字段/事件/错误码），有偏差则改上游文档并写进提交信息 （specs/002-sandboxed-tools/contracts/api.md）
- [ ] T075 全量质量门禁：`ruff check` + `mypy` + `pytest`（含集成）+ `eslint` + `tsc` + `vitest` + `vite build` 全绿；CI 增沙箱镜像构建步骤 （backend/pyproject.toml + frontend/package.json）
- [ ] T076 quickstart 验证：在 Linux 宿主真实栈执行 S1~S6 + 越权矩阵，如实更新 quickstart.md 的 SC 对照与验证记录表（通过才 ✅，未过保持 ⏳） （specs/002-sandboxed-tools/quickstart.md）

**Checkpoint**: P2「跑脚本」验收口径达成，可独立上线

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，可立即开始（T003/T004/T005 可并行）
- **Foundational (Phase 2)**: 依赖 Setup——**阻塞全部用户故事**
- **US1 (Phase 3)**: 依赖 Phase 2（T015 沙箱客户端 + T011 catalog）
- **US2 (Phase 4)**: 依赖 Phase 2；与 US1 无数据依赖，但 US1 新增的两个沙箱工具要靠 US2 才能按 Agent 粒度开放，故排在其后
- **US3 (Phase 5)**: 依赖 US1（要有执行才有产物）
- **US4 (Phase 6)**: 依赖 US1（记录来自执行）
- **US5 (Phase 7)**: 依赖 Phase 2（T016 求值）与 US1（要有执行才有出站）
- **Polish (Phase 8)**: 依赖全部故事

### User Story Dependencies

- **US1 (P1)**: MVP 起点，无前置故事
- **US2 (P1)**: 可与 US1 并行开发（改的是不同文件：catalog/schema/runner vs sandbox 执行器），但合并顺序建议 US1 → US2
- **US3 (P2)**: 强依赖 US1
- **US4 (P2)**: 强依赖 US1；T053（信号量）与 US3 无耦合，可并行
- **US5 (P2)**: 依赖 US1 的执行钩子；与 US3/US4 并行度最高（不同文件：egress.py/egress_service.py/admin.py）

### Within Each User Story

- 测试 MUST 先写并 FAIL，再实现使其 PASS
- 实现顺序：service → executor/endpoint → 前端 → 审计 → 集成验证
- 涉及「拒绝/拦截/上限」的实现，必须反向验证一次（把判断去掉，测试应变红）

### Parallel Opportunities

- Phase 1：T003/T004/T005/T007 并行
- Phase 2：T011/T012/T013/T014/T016 并行（都以 T008/T009 的模型为输入）
- 每个故事内的 `[P]` 测试任务并行
- Phase 8：T068/T069/T070/T072/T073 并行

---

## Parallel Example: User Story 5

```bash
# 先并行写测试（红）：
Task: "策略求值单元测试 backend/tests/unit/test_egress_eval.py"
Task: "记录归并单元测试 backend/tests/unit/test_egress_record.py"
Task: "出站 API 集成测试 backend/tests/integration/test_egress_api.py"

# 再并行实现互不依赖的模块：
Task: "出站服务 backend/app/services/egress_service.py"
Task: "管理端点 backend/app/api/v1/admin.py"
Task: "Agent 出站端点 backend/app/api/v1/agent_egress.py"
```

---

## Implementation Strategy

### MVP First（US1 + US2）

US1 是能力、US2 是这能力的正确开关。**两者必须同批交付**——只做 US1 会出现「新工具对所有 Agent 默认开放」，正是本特性要修的缺陷；只做 US2 则是把不存在的工具名改成另一批不存在的工具名。

1. Phase 1 + Phase 2（设施与基础）
2. Phase 3（US1）→ 独立验证 S1
3. Phase 4（US2）→ 独立验证 S3
4. **STOP and VALIDATE**：US1+US2 = 可演示的「Agent 真的能跑代码，且只听自己被授权的工具」

### Incremental Delivery

1. Setup + Foundational → 设施就绪
2. US1 → S1 通过 → 演示「跑脚本」
3. US2 → S3 通过 → 演示「工具授权」
4. US3 → S4 通过 → 演示「产出可下载」
5. US4 → S6 通过 → 演示「可观测与配额」
6. US5 → S5 通过 → 演示「出网记录与黑名单」
7. Polish → 真实栈 + 越权矩阵 → 可上线

### 提交节奏（宪法强制）

- Phase 1：`feat(sandbox): 沙箱镜像与部署设施（T001-T007）`
- Phase 2：`feat(sandbox): 数据层/工具目录/容器封装/出站求值（T008-T017）`
- Phase 3：`feat(sandbox): 隔离执行与实时输出（T018-T031）`
- Phase 4：`feat(tools): 目录单一来源与授权生效（T032-T041）`
- Phase 5：`feat(artifacts): 脚本产出入库与下载（T042-T047）`
- Phase 6：`feat(observability): 执行计量与并发配额（T048-T056）`
- Phase 7：`feat(egress): 出站记录/黑名单/限制模式（T057-T067）`
- Phase 8：`feat(polish): 真实栈验证/越权矩阵/保留策略/文档（T068-T076）`
- 每个 Phase 后：勾选 tasks.md 并提交 `docs(tasks): 更新 002-sandboxed-tools 任务状态`

---

## Notes

- [P] = 不同文件、无依赖
- [Story] 标记用于追溯；未标故事的多为 Phase 1/2 与 Polish 的横切任务
- 测试未过前禁止勾选实现任务（TDD 纪律）
- **必须反向验证的三处**：T020（失败不抛异常）、T034（越权阈值）、T049（信号量释放）——去掉判断后测试应变红，否则是假绿
- **环境限制**：iptables/conntrack 相关验证（T060/T069/T070）只能在 Linux 宿主跑；macOS 本地开发跳过并在提交信息里注明
- 已知未覆盖：SC-004 的 P95 对比需压测（P3 才做全量压测），本特性用手工 20 并发观察并记录
- 未决项（宪法 Follow-up）：gVisor/seccomp 加固归 P3；本特性只在 deploy/README 记录 socket 风险

---

## 实现偏差记录（T015 / T024 收口，2026-11-14）

T015 原计划里有 5 处与真实 Docker 行为不符，**全部经真实容器探针实测确认**（19 个探针脚本，
本机 Docker 29.9.3 / SDK 7.2.0），已按实测改写实现。逐条记录如下，便于 code review 与后续
T023/T024 对齐：

| # | 计划原文 | 实测结论 | 实际实现 |
|---|---|---|---|
| 1 | 脚本 `put_archive` 写入 `/workspace/main.py` | `read_only=True` 时 daemon **无条件**拒绝 `put_archive`（400 `container rootfs is marked read-only`），即使目标是 tmpfs 挂载点也不行——daemon 只看 rootfs 标记，不看目标路径 | 脚本写入**命名卷** `/code`（卷不受 rootfs 只读限制），路径改 `/code/main.py` / `/code/run.sh`。语义不变：`command` 仍是常量，用户内容仍是数据（R-07） |
| 2 | `containers.run(detach=True, remove=False)` | `run()` 会**立刻启动**容器，而脚本必须先于启动写入，否则 tmpfs 挂载会遮蔽已写入的文件 | 改为 `create → put_archive → start` 三步。`fake_docker` 里把 `containers.run` 写成 `raise AssertionError`，从测试层锁死这个偏差 |
| 3 | 产物从 `/workspace/output` 取 | tmpfs 上的产物随容器退出**卸载丢失**（`get_archive` 只剩空目录项 `[('output', 0, True)]`） | `/workspace/output` 单独挂**命名卷**，产物回收发生在容器退出之后仍可读 |
| 4 | `storage_opt={"size": "256m"}` 限制磁盘 | **不生效**：写满 400MB 全部成功（底层 overlay2 未开 project quota） | 磁盘限额只能靠 **tmpfs `size=`**（实测写 400MB 时返回 `ENOSPC: 28`）。`storage_opt` 不写进实现 |
| 5 | `wait(timeout=1)` 抛 `ReadTimeout` | 实际抛 `requests.exceptions.ConnectionError`（"Read timed out" 被 urllib3 包了一层），**不在** `docker.errors` 里 | 超时轮询统一捕获 `requests.exceptions.RequestException` |

**遗留缺口（转 T044 收口）**：`output/` 命名卷没有内核级大小上限（`storage_opt` 不可用）。
当前靠「采集侧硬上限（`_ARCHIVE_READ_CEILING = 512MB` 边读边判）+ 采集后立即删卷」兜底，
即最坏情况下容器可在卷里写满宿主磁盘。彻底方案需宿主开启 overlay2 project quota 或改用
单独的小分区挂载，属部署侧改造。

**内置出站黑名单的实现位置**：契约 `contracts/` 写的是 DB 里 `builtin=true` 的行，实际实现
用的是配置常量 `EGRESS_BUILTIN_BLACKLIST`（`app/core/config.py`）。理由：内置项属于代码资产
（改它要发版），放 DB 会让「谁能改内置黑名单」变成权限问题。DB 表只存用户/全局自定义规则。
后续如需要运营侧可调，再迁到 DB 并加 `builtin` 列。
