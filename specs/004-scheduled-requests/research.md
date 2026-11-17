# Research: 定时调度（按规则自动建单并分配执行）

**Feature**: 004-scheduled-requests
**Date**: 2026-11-17

本文件记录实现手法的取舍。所有带数字的结论都是在写规格/计划期间于本机实测得到的，
标注了实测命令所在位置；没有实测支撑的，明确写为「未验证」。

---

## R-01: 调度器复用既有 Celery Beat，不引入第二套调度设施

- **Decision**: 用已有的 Celery Beat（`deploy/docker-compose.yml` 的 `beat` 服务）新增一条
  每分钟一次的扫描任务，由它找出到期规则并逐个处理。不引入 APScheduler、不引入
  django-celery-beat、不引入外部 cron。
- **依据（实测）**:
  - 仓库已有 Beat 在跑两条周期任务（`backend/app/tasks/celery_app.py:32` 起的
    `beat_schedule`：`reap-stale-runs` 每 5 分钟、`purge_expired_sandbox_data` 每天 3 点），
    部署里 `beat` 是单实例服务（`deploy/docker-compose.yml` 的 `beat:`，命令为
    `celery -A ... beat`）。新增一条 schedule 是零新增基础设施。
  - 时区已在 `celery_app.conf` 里设为 `Asia/Shanghai`，与规格要求的求值时区一致，
    不需要在两处维护时区。
- **被否决的方案**:
  - **APScheduler**：需要在 API 或独立进程里再起一套调度器，多一个进程要监控、
    多一套持久化 job store、且与 Beat 的时区/单实例语义并存会产生「两套定时器谁负责什么」
    的长期困惑。为零新增能力付出这些代价不划算。
  - **系统 cron 调用 HTTP**：无法在应用内做行级锁与幂等，且把「谁有权改计划」泄漏到
    部署层；容器化后还要额外挂 crontab。
  - **django-celery-beat**：项目不是 Django 技术栈（宪法固定 FastAPI + SQLAlchemy），
    它的价值在管理界面，而我们需要的是业务侧的调度规则表，自带界面没有意义。

## R-02: 到期判定用「扫描 + 每规则行锁」，不用「每规则一个 Celery 定时任务」

- **Decision**: 数据库里存规则与 `next_run_at`；扫描任务每次取 `next_run_at <= now`
  且启用的规则，逐条加行锁处理后推进 `next_run_at`。**不为每条规则创建 Celery 周期任务**。
- **依据（实测）**:
  - Celery 的周期任务是**静态配置**（`beat_schedule` 在进程启动时读入）。规则由用户在运行时
    增删改，若为每条规则建周期任务，就必须在每次 CRUD 后重启或热改 Beat 的调度表，
    并处理「Beat 重启后旧任务残留」「删除规则但任务还在跑」等问题。
  - 扫描成本可忽略：实测本机 `crontab` 解析 + 计算下次触发 **0.015 ms/次**（2000 次循环取均值，
    含对象构造），纯计算下次触发 **0.003 ms/次**。即便一次扫描 200 条规则，计算开销约 3 ms，
    远小于一次数据库往返。
- **被否决的方案**:
  - **每规则一任务**：见上，静态配置与动态规则根本冲突。
  - **只在内存里维护下次触发**：进程重启即丢；且无法与「多执行者」共存（虽 v1 单执行者，
    但这个约束不该被实现细节固化成不可扩展）。

## R-03: 幂等靠「计划时刻唯一约束」，不靠 Redis 锁

- **Decision**: 触发记录表对 `(schedule_id, planned_at)` 建唯一约束。处理一条规则时，
  先尝试插入 `planned_at = 该规则的 next_run_at` 的触发记录；插入冲突即说明这一次计划时刻
  已被处理过，直接跳过。**不使用 Redis 分布式锁来保证"只建一张单"**。
- **依据（实测）**:
  - 项目已有 Redis 锁的用法（`backend/app/tasks/execute_run.py` 的 `_acquire_lock`，
    `SET NX EX` 加锁、执行完释放）。但那把锁解决的是「同一 Run 被重复投递」，
    其正确性依赖**释放**：进程被 kill 时锁靠 TTL 过期，期间重复投递会被拒。
    对调度而言，我们要的是「同一个计划时刻永远只建一张单」这个**长期事实**，
    而 Redis 的键是会过期的、也会被 flush，不能作为事实来源。
  - 唯一约束是数据库层的硬保证，不依赖任何进程状态，且天然覆盖「重复投递」
    「并发处理」「进程崩溃后重试」三种情形。
- **被否决的方案**:
  - **Redis 锁**：TTL 到期后锁消失，若此时下一次扫描仍读到旧 `next_run_at`（比如推进
    `next_run_at` 的那一步失败了），就会重复建单。锁只防同时，不防重放。
  - **检查「是否已有相同 planned_at 的需求」**：需求表没有该字段，加字段等于把调度语义
    塞进通用实体；且查询-再插入本身不是原子的，仍需唯一约束兜底。

## R-04: 「错过不补跑」判定为「超出宽限期才记错过」，且在记录里合并

- **Decision**: 扫描时若 `now - next_run_at > 宽限期`（宽限期 = 一个处理周期 + 容差，
  取 90 秒，与 SC-002 对齐），则判为错过：计算 `(next_run_at, now]` 区间内按表达式应发生的
  次数，写**一条**合并的错过记录（含次数与窗口起止），然后把 `next_run_at` 推进到 `now` 之后
  的下一个计划时刻，**不创建任何需求**。
- **依据（实测）**:
  - 滚动计算「若干计划时刻」的循环在 R-02 的成本内（0.003 ms/次）；对 2 小时停机、
    每 5 分钟的规则，需要迭代 24 次算次数，总开销约 0.07 ms。
  - 边界实测：`crontab` 的 `remaining_estimate` 在「当前时刻正好等于计划时刻」时返回的是
    **下一个**计划时刻（实测 `2026-11-17 09:00` → next `11-18 09:00`），因此推进逻辑必须
    显式用「严格大于」的比较，不能依赖框架替我们区分「刚好到点」与「已经过了」。
- **被否决的方案**:
  - **每错过一个时刻写一条记录**：2 小时停机 × 每 5 分钟 = 24 条；一天停机 × 每分钟 = 1440 条。
    这正是 FR-015 要禁止的记录爆炸。
  - **不记录、只推进**：用户会认为「它应该跑过」，无从判断是错过还是没配置。
  - **宽限期取 0（只要过了就记错过）**：扫描周期本身是 1 分钟，正常调度也会有几十秒延迟，
    宽限期为 0 会把每一次正常触发都记成错过。

## R-05: 触发链路的原子性复用 `assign()` 自身的事务边界

- **Decision**: 处理一次触发时，在**同一个数据库会话**里先建需求并 `flush`（不提交），
  再调用既有的 `assignment_service.assign(...)`。`assign()` 内部结尾会 `commit`，
  因此需求与 Run **一起提交**；`assign()` 抛错时整体回滚，不会留下「已创建但未分配」的孤儿卡片。
- **依据（实测）**:
  - 读代码确认 `assignment_service.assign`（`backend/app/services/assignment_service.py:144`）
    的顺序是：需求行锁 → 配额校验 → 状态校验 → 目标校验 → 策略展开 → 建 Run → `await db.commit()`
    → 提交后才发布事件与入队。也就是说 **commit 在函数内部**，调用方无法在外面单独控制，
    但也因此"需求 + Run"天然共享同一次提交。
  - `assign()` 的第一句是 `get_request(..., for_update=True)`，要求需求行已存在，
    所以必须先 `flush` 而不是 `commit`（`commit` 会让后续失败无法回滚需求）。
  - **自锁风险已实测排除**：同一会话内先 `INSERT` 需求行并 `flush`，紧接着对该行
    `SELECT ... FOR UPDATE`，在真实 PostgreSQL 16 上正常返回（不阻塞、3 秒超时未触发）。
    原理是同一会话对自己持有写锁的行再加行锁不会与自身冲突。若这里会自锁，
    R-05 的「同一会话建单 + 调 assign」方案根本不成立，所以这条必须先验。
- **被否决的方案**:
  - **先提交需求再调分配**：`assign()` 失败（目标已归档、配额耗尽）时，需求已经落库，
    变成永远停在 `created` 的孤儿卡片，直接违反 FR-009。
  - **自己重建一套「建 Run + 入队」逻辑**：会绕开 `tool_grants_snapshot` 冻结、
    `snapshot_external_grants`、策略展开、配额熔断等既有保障，从而违反 FR-008
    与 SC-003（结构必须与手工分配 100% 一致）。

## R-06: 「前次未结束则跳过」的判定对象是该调度最近一次触发的需求

- **Decision**: 判定「是否忙」时，取**该调度最近一次成功触发所关联的需求**，看它是否处于非终态
  （`created`/`assigned`/`running`）。不是「目标 Agent 上是否有任何非终态 Run」。
- **依据（实测）**:
  - `Request.status` 的取值在 `backend/app/models/request.py` 里是
    `created/assigned/running/completed/failed/cancelled`；`running` 由分配置位，
    终态由 `request_service.settle_request_status` 收口（该函数在 worker 的 finally、
    fatal 路径、配额拒绝路径、取消端点都会被调用）。
  - 若按「目标 Agent 是否忙」判定，用户手工拖给同一个 Agent 的卡片会阻塞定时任务——
    这是两件无关的事，而且用户无法从调度页面看出"为什么没跑"。
- **被否决的方案**:
  - **按目标 Agent 忙闲判定**：跨调度互相干扰，且与配额/并发控制重复。
  - **不判定，直接跑**：与 FR-013 冲突，且成本会随触发频率无上限叠加。

## R-07: 周期表达式直接复用 Celery 的 `crontab`，并显式声明「交集」语义

- **Decision**: 不引入 `croniter`，用 Celery 自带的 `crontab` 解析与求值；在规格与界面上
  明确「日期与星期同时限定时取交集」。
- **依据（实测）**:
  - 参数顺序有坑：`crontab(minute, hour, day_of_week, day_of_month, month_of_year)` ——
    **`day_of_week` 在 `day_of_month` 之前**。直接按标准 cron 顺序（分 时 日 月 周）传位置参数
    会解析错。实测错误顺序下 `crontab("0 9 * * 1-5")` 抛
    `ValueError: Invalid weekday literal`；按关键字正确映射后 `dow=[1,2,3,4,5]`、`dom=[1..31]`。
  - 语义确认：`0 9 1 * 1`（每月 1 号 09:00 且周一）实测从 `2026-11-17` 起依次得到
    `2027-02-01`、`2027-03-01`、`2027-11-01`、`2028-05-01`、`2029-01-01` —— 全是"既 1 号又周一"，
    即**交集**；标准 cron 此处是并集。这是规格 Assumptions 第 1 条的实测来源。
  - 非法表达式的报错可读性够用，可直接作为用户提示：`60 * * * *` →
    `Invalid end range: 60 > 59`；`0 0 31 2 *` → `Invalid crontab: day_of_month '31' never occurs
    in month_of_year '2'`（正好覆盖 US1 场景 3「永远不会发生」）。
  - 时区：`crontab` 的 `tz` 不是构造参数（实测传 `tz=` 抛
    `TypeError: BaseSchedule.__init__() got an unexpected keyword argument 'tz'`），
    但它是类的 `cached_property`，可**在实例上赋值**。赋值后实测
    `2026-11-17 08:00 SH` → next `09:00 SH`、`09:00 SH` → next 次日 `09:00 SH`、
    `周五 18:00` → next `下周一 09:00`，跨周末与"正好到点推到下一次"都正确。
  - 闰年边界实测：`0 0 29 2 *` 从 `2026-03-01` 得到 `2028-02-29`，跨过非闰年的 2027。
- **被否决的方案**:
  - **croniter**：需新增依赖（当前 `pyproject.toml` 无此包，实测 `import croniter` 失败），
    而 Celery 已经间接依赖并提供了等价求值能力。宪法基调是零冗余依赖。
  - **自研解析器**：cron 的边界（`1-5,0`、闰年、月末）容易写错，而这些边界正是
    调度器最容易出事故的地方；实测已证明 `crontab` 能正确处理 `1-5,0` 与闰年。
  - **给 `tz` 传构造参数**：不可用（见上），只能实例赋值，这一点必须写进代码注释，
    否则后来者会以为可以传参。

## R-08: 触发历史用独立表 + 唯一约束承载幂等与审计，需求表只加一个可空外键

- **Decision**: 新增 `schedule_triggers` 表记录每次处理的计划时刻、结果、原因、关联需求；
  `schedules` 表存规则；`requests` 表**只新增一个可空的来源调度外键**，不改任何既有列语义。
- **依据（实测）**:
  - 现有 `requests` 表实测列（`docker exec aw-postgres psql -c "\d requests"`）：`id, owner_id,
    title, description, expected_output, priority, status, attachments, created_at, updated_at`。
    **没有 `deadline` 列** —— 这修正了 `docs/architecture.md` 数据模型里"requests 有 deadline"
    的描述，也是规格 Assumptions 第 4 条的来源。因此模板不能承诺截止时间。
  - `backend/app/schemas/request.py` 的 `RequestCreate` 只接受 title/description/
    expected_output/priority，进一步印证模板范围。
  - 迁移脚本现有编号到 `0006_artifact_run_step_index`，新增迁移须接在其后。
- **被否决的方案**:
  - **把触发历史塞进 `audit_logs`**：审计是 append-only 的安全记录，用它承载业务查询
    （按结果过滤、分页、跳转需求）会把安全记录变成业务表，且审计的保留策略与业务不同。
  - **把 `planned_at` 存进 requests**：违反上面 R-03 的取舍，且需求是通用实体，
    不该因为一个来源而多出调度专属字段。

## R-09: 触发记录保留用「每日维护任务」，与既有沙箱清理同一处

- **Decision**: 复用已有的 `purge_expired_sandbox_data` 每日维护任务模式，新增触发历史清理：
  每调度保留最近 N 条（默认 500）与 N 天（默认 90 天，与沙箱数据保留期一致），
  同时把汇总字段（最后触发、累计计数、连续失败数）留在 `schedules` 行上。
- **依据（实测）**:
  - `backend/app/tasks/maintenance.py` 已有 `SANDBOX_DATA_RETENTION_DAYS = 90` 的清理实现，
    模式（按时间 cut-off 批量删、返回清理条数）可直接沿用。
  - `backend/app/tasks/celery_app.py` 已有 `purge-expired-sandbox-data` 在每天 3 点跑，
    新增一条同时间附近的任务不引入新的调度维度。
- **被否决的方案**:
  - **不清理**：单条分钟级规则一年约 52 万条，历史表会显著膨胀且拖慢按调度查询。
  - **只按天数清理**：高频规则在 90 天内仍会远超合理量级，需要条数上限兜底。

## R-10: 前端新增调度管理页，复用既有列表/表单/查询模式

- **Decision**: 新增 `/schedules` 页（列表 + 新建/编辑 + 触发历史抽屉），复用既有
  TanStack Query 数据获取、既有 UI 组件（`frontend/src/components/ui`）、既有表单校验风格；
  导航栏加入口。触发历史的详情跳转复用既有需求详情路由。
- **依据（实测）**:
  - 现有页面清单：`frontend/src/pages` 下 `BoardPage/DashboardPage/AgentPage/GroupPage/
    MarketplacePage/RunDetailPage/AdminPage/LoginPage/RegisterPage`，已有 `components/ui` 组件库，
    路由级 `lazy()` 代码分割已落地（首屏 gzip 从 152KB 降到 102KB），新增页面按同一方式接入即可。
- **被否决的方案**:
  - **塞进看板页一个抽屉**：调度是"规则管理"，与看板"卡片流转"是两种心智模型；
    混在一起会让看板每次加载都多拉一次调度数据。

## 未验证 / 待实现阶段确认的点

以下各项在本次研究阶段**没有**取得实测数据，必须在实现阶段用可执行动作验证，
不得凭推理标注完成：

1. **SC-006 的 20 并发劣化表现**：需要在真实栈上跑，复用 002/003 已建立的归因口径
   （含 150ms 绝对上限）。本机未起完整栈，未测。
2. **Beat 每分钟扫描对 Worker 的影响**：需在真实栈观察扫描任务是否与执行任务竞争，
   以及 `beat` 容器 0.5 CPU / 256MB 的内存限额是否足够。
3. **~~`assign()` 在同一会话中被调度调用时的行锁行为~~（已实测，见 R-05）**：
   同一会话对自己刚 `flush` 的需求行执行 `FOR UPDATE` 不会自锁，已在真实 PostgreSQL 16 验证。
4. **触发历史保留策略的实际数据量**：默认 500 条/90 天的合理性需用真实触发频率校验。
