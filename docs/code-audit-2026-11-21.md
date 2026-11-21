# 代码审查报告（2026-11-21）

范围：`backend/app`（166 文件 / 30.4k 行）+ `frontend/src`（151 文件 / 25.0k 行）+ `e2e/`。
方法：先跑全量质量门禁拿客观基线，再按模块人工精读（执行器与编排、服务与 API、前端状态与渲染），
最后**逐条回读源码核实**——代理报出的结论不直接采信，本文每条都标了能否从代码直接验证。

## 零、基线：质量门禁全绿

| 门禁 | 结果 |
|---|---|
| `ruff check app tests` | All checks passed |
| `mypy app` | Success: no issues found in 166 source files |
| `tsc --noEmit` | 0 error |
| `eslint src` | 0 error / 0 warning |
| `pytest`（后端全量） | **2124 passed**，6 subtests |
| `vitest`（前端全量） | **155 passed**（31 文件） |

> 修复完成后（同日晚）复测：后端 **2141 passed**（+17，均为本轮新增回归用例）、
> 前端 **167 passed / 35 文件**（+12）。全部门禁仍为绿。

结论：**形式层面的检查已经没有优化空间**。下面的问题全部是类型检查、lint、测试都发现不了的
——确定性的逻辑缺陷、重复导致的漂移风险、以及架构层的 IO/渲染浪费。

---

## 修复状态（2026-11-21 当日逐条修复）

原则：一条一提交、每提交带回归测试，且**先反向验证**——把修复回退，确认新增断言
真的变红，再恢复。纯性能问题无法靠断言结果捕捉，改用 SQLAlchemy
`before_cursor_execute` 数 SELECT 次数。

| 编号 | 问题 | 状态 | 提交 | 反向验证证据 |
|---|---|---|---|---|
| D1 | 拖拽分配因详情缓存崩溃 | ✅ 已修 | `e37b606` | 回退守卫后用例抛 TypeError |
| D2 | 深链详情「关闭」关不掉 | ✅ 已修 | `677d12e` | 断言 `?request=` 未清空 |
| D3 | 执行日志被重连清空 | ✅ 已修 | `9db32cf` | 断言日志条数为 0 |
| D4 | 调度规则永久停触发 | ✅ 已修 | `ee97dc4` | 注入 audit 失败复现 next_run_at 不推进 |
| D5 | stdio 池竞态 / 跨循环不可用 | ✅ 已修（**比原报告严重**） | `851e90a` | 8 并发协程 → 8 次 spawn |
| D6 | 执行锁释放不校验持有者 | ✅ 已修 | `dfe58a1` | B 的锁被 A 删掉 |
| P1 | `list_schedules` 列表 N+1 | ✅ 已修 | `ee85349` | SELECT 次数 7 → 22（随条数增长） |
| P2 | `_persist_steps` 逐条 SELECT + 整批重放 | ✅ 已修 | `9b55e3e` | 2 步 → 2 次多余 SELECT；10 预建 → 10 次 |
| P3 | `_load_context` 白加载历史 steps | ✅ 已修 | `1be23e1` | `inspect(run).unloaded` 含 steps |
| P4 | MinIO 同步上传阻塞事件循环 | ⏸ 保留 | — | 见下方说明 |
| P5 | 每秒十余次 Docker API 往返 | ⏸ 保留 | — | 见下方说明 |
| P6 | 全局 staleTime: 0 | ✅ 已修 | `fd85bb4` | — |
| P7 | 分页闪空 + 看板全量重算 | ✅ 已修 | `fd85bb4`、`8a86d84` | 回退 memo 后 `$$typeof` 断言变红 |
| R1–R5 | 重复与漂移 | ⏸ 记录待办 | — | 属于「下次改动会漏」类，见第五节 |

**D5 在修复过程中查出比原报告更严重的问题**：原报告只说「并发首次访问会泄漏子进程」，
实际 `execute_run` 每次调用都 `asyncio.run()` 起新事件循环，池里的条目全部绑在
已关闭的旧循环上 —— 第二次工具调用必然抛
`RuntimeError: got Future attached to a different loop`，且连 `close()` 都跑不了。
也就是说 024 的常驻池在生产形态下**第二步就崩**，此前完全没有覆盖（测试都在同一个
pytest-asyncio 循环里跑）。

**P4 / P5 为何本轮不做**：两者都需要实测数据才能决定值不值得改——P4 的阻塞只发生在
API 层 presign（`02cfa06` 已把 MinIO 移出事件循环的那一处覆盖不到新路径），
改成异步要重写上传链路；P5 的 Docker API 往返是沙箱状态轮询的固有代价，
降频会牺牲取消响应速度。在没有真实压测基线的前提下动手，风险大于收益。

---

## 一、确定性缺陷（已核实，建议优先修）

这一类不是"可以更好"，而是"确实会出错"。三条都能从代码直接确认。

### D1【前端·高】拖拽分配会因详情缓存而崩溃　✅ 已修 `e37b606`

`frontend/src/hooks/useRequests.ts:66-83`

```ts
const prevEntries = qc.getQueriesData<{ items: WorkRequest[] }>({ queryKey: ['requests'] })
for (const [key, data] of prevEntries) {
  if (!data) continue
  qc.setQueryData(key, { ...data, items: data.items.map(...) })   // ← data.items 可能是 undefined
}
```

`['requests']` 前缀下混了两种**结构完全不同**的缓存：

- 列表：`['requests', scope, project]` → `{ items: WorkRequest[], total }`（BoardPage:126）
- 详情：`['requests', 'detail', deepLinkId]` → **单个 WorkRequest**，无 `items`（BoardPage:212）

从通知点进一条不在首页的需求（走深链 → 详情被单独缓存）→ 再拖任意卡片分配 →
`data.items` 为 `undefined` → `undefined.map` 抛 TypeError → `onMutate` 失败 → **拖拽无任何反馈**。

**修法**（一行 type guard，先止血）：

```ts
const list = (data as { items?: unknown }).items
if (!Array.isArray(list)) continue
```

**根治**：拆前缀 —— 列表 `['requests','list',...]`、详情 `['request', id]`。
同一个根因还生了下面 D2、见 M1、M2 三处问题。

### D2【前端·高】深链打开详情后，「关闭」按钮关不掉　✅ 已修 `677d12e`

`frontend/src/pages/BoardPage.tsx:453` vs `:95-101`

```tsx
// 遮罩/ESC/X 都走 closeDetail()，会清 URL 参数
const closeDetail = () => { setDetail(null); if (deepLinkId) { ...delete('request')... } }

// 但 footer 的「关闭」只清 state
<Button variant="secondary" onClick={() => setDetail(null)}>关闭</Button>
```

`currentDetail` 的来源是 `detail ?? inList ?? deepLinkRequest`。深链场景下 `detail` 恒为
`null`，点「关闭」后 `inList` 仍在 → Modal 继续渲染，**界面纹丝不动**，只能点遮罩或 ESC。

同样问题在 `accept` 成功（:184）、`reject` 成功（:192）两处回调里，都是裸 `setDetail(null)`。

**修法**：三处统一改 `closeDetail`。

### D3【前端·高】执行日志在每次重连/轮询时被清空　✅ 已修 `9db32cf`

`frontend/src/pages/RunDetailPage.tsx:104-124`

```ts
const load = useCallback(async () => {
  ...
  setRun(await api.fetchRun(runId))
  setLogs([])          // ← 意图是「切 run 时清」，但 load 有三个非切 run 的调用方
  setReplyMessage('')
}, [runId])
```

`load` 的调用方：

| 位置 | 场景 |
|---|---|
| :126 | 首屏 / 切 run（这个该清） |
| :314-318 | WS 重连成功后补数 |
| :323-327 | WS 放弃后每 10 秒降级轮询 |

再叠加 `useRunSocket` 把 `accessToken` 放进 effect 依赖（:174），任何一次 401 静默续期
都会重建 WS → `connected` 跳变 → 触发 `load()` → **日志全丢**。

实际表现：WS 一进入 failed 状态，用户已看到的日志**每 10 秒被清一次**；长会话里日志基本存不下来
（缓冲区仅 200 行且无回放）。

**修法**：把「清空」从 `load` 拆出来，只跟 `runId` 走：

```ts
useEffect(() => { setLogs([]); setReplyMessage('') }, [runId])

const load = useCallback(async () => {
  if (!runId) return
  setError(null)                          // 补数不该整页骨架屏
  try { setRun(await api.fetchRun(runId)) } ...
}, [runId])
```

同时 `useRunSocket` 的 token 改用 `ref` 持有，token 变化不重建连接（WS 只在握手时校验一次，
续期对已建连接无意义）。

### D4【后端·中】调度规则在某些失败路径下会永久停止触发　✅ 已修 `ee97dc4`

`backend/app/services/schedule_service.py:730-754` + `:928`

```python
except DependencyBlockedError:
    await db.commit()               # 736：需求已独立提交
    return await _finish_trigger(   # 742：只改内存，不提交
        ..., next_run=next_after(...)   # ← next_run_at 推进只在这个未提交的改动里
    )
```

`_finish_trigger`（:484-527）只赋值不 commit。这个分支的落库完全依赖外层
`run_due_schedules` 在 **:928** 的那次 commit。而它前面是 `write_audit(...)`，
`write_audit` 在 flush 失败时会 `await db.rollback()` 并抛 `AppError`（`core/audit.py:145-158`）。

于是：需求已提交 → 触发记录与 `next_run_at` 推进被回滚 → 下一轮扫描该规则仍到期 →
`_insert_placeholder` 命中 `ON CONFLICT DO NOTHING` 返回 `False` → 直接 `return None`（:611）
→ **永远不再推进**。需求建了、规则死了，日志只有一行 warning。

触发条件较窄（审计写失败，通常是磁盘满或 DB 故障），但后果是永久性静默失效。

**修法**（5 行，把 commit 提到 `_finish_trigger` 之后）：

```python
except DependencyBlockedError:
    trigger = await _finish_trigger(db, schedule, planned, result="fired", ...)
    await db.commit()      # 需求 + 触发记录 + next_run_at 原子落地
    return trigger
```

其他分支（:639/659/684/788/863）都是「先改后提」，只有这一支是「先提后改」，属于分支不一致。

### D5【后端·中】stdio 池懒加载有竞态，会泄漏子进程　✅ 已修 `851e90a`（并查出跨循环崩溃）

`backend/app/executors/stdio_pool.py:111-119`

```python
async def _get_entry(spec: StdioSpec) -> _Entry:
    entry = _entries.get(spec.key)
    if entry is not None:
        return entry
    client = spec.client()
    await client.__aenter__()        # ← 真实 spawn（npx 冷启动数秒），期间让出事件循环
    entry = _Entry(client=client, lock=asyncio.Lock())
    _entries[spec.key] = entry       # ← 后写者覆盖，先启动的进程变成孤儿
    return entry
```

两个协程同时首次访问同一 stdio MCP server 时，会各起一个子进程，字典只留一个，
另一个永远没人 `terminate`（`close_all` 只遍历字典）。

**修法**：单个模块级 `asyncio.Lock` 保护 `判空 → spawn → 写入` 这一段；或 per-key 创建锁 +
`setdefault` 复用已存在的 entry 并关掉多余的那个。

顺带：`close_all()`（:206）全仓只有测试调用，docstring 承诺的「worker 退出时调用」并不存在 →
应接到 Celery `worker_process_shutdown` 信号，否则优雅重启会攒下孤儿 `npx` 进程。

### D6【后端·低概率但后果重】执行锁释放不校验持有者　✅ 已修 `dfe58a1`

`backend/app/tasks/execute_run.py:70-86`

```python
def _acquire_lock(run_id): client.set(f"run:lock:{run_id}", "1", nx=True, ex=3600)
def _release_lock(run_id): client.delete(f"run:lock:{run_id}")   # ← 无条件删
```

对比同项目 `sandbox_service.release_concurrency_slot`（:49-65）已经用 Lua CAS 做对了这件事，
这里没有。若 Worker 被 SIGKILL 卡过 TTL（3600s）后恢复，它会删掉**新持有者**的锁 →
第三个 Worker 又能拿到 → 同一 Run 并发执行（`seq` 唯一约束冲突、成本重复累加）。

概率低（正常执行受 `max_run_seconds` 1800s 约束，远短于 TTL），但修法就是复用已有的 CAS 片段，
成本几乎为零，建议顺手统一。

---

## 二、性能（可量化，风险低）

### P1【后端】`list_schedules` 是列表 N+1　✅ 已修 `ee85349`

`backend/app/services/schedule_service.py:421-427` → `_to_out`（:204-211）

```python
return [await _to_out(db, s, user) for s in rows]     # 每条一次 db.get(目标)
```

`_to_out` 内部 `_load_target_display` 每次 `db.get(Agent|Group)`；admin 视角再多一次 `db.get(User)`。
`page_size=20` → member 20 次、admin 最多 40 次串行往返。

**对比**：同仓库 `request_service.assignee_emails`、`dependency_service.blocked_summaries`
都已做了一次 `IN` 批量取 —— 这是双标准，不是设计取舍。

**修法**：拿到 rows 后一次批量查三张表（`Agent.id.in_(...)` / `AgentGroup.id.in_(...)` / `User.id.in_(...)`），
`_to_out` 拆成接收预取字典的纯函数。20 条从 20~40 次降到 2~3 次。

### P2【后端】`_persist_steps` 逐条 SELECT，且整批重复执行一次　✅ 已修 `9b55e3e`

`backend/app/tasks/execute_run.py:138-175`

```python
for step in steps:
    persisted = await db.get(Step, step["id"])   # 每条一次 SELECT
```

调用点：`save_step` 每条调一次（:594-602，且每次额外 `await db.commit()`），
而 `:775 / :787 / :791` 又各把**全部** steps 再跑一遍 `_persist_steps`。

一次 10 步的 Run ≈ 20 次 `SELECT steps` + 十余次 commit。结尾整批那次与逐条 `save_step` 完全重复。

**修法**：`select(Step).where(Step.id.in_(ids))` 一次取回建 dict；`save_step` 内不 commit，
交给每个 LLM 轮次结束统一提交一次。

### P3【后端】`_load_context` 白加载整段历史 steps　✅ 已修 `1be23e1`

`backend/app/tasks/execute_run.py:91`

```python
stmt = select(Run).options(selectinload(Run.steps)).where(Run.id == run_id)
```

`run.steps` 下游**从未使用**（全仓 grep：仅此一处出现 `selectinload(Run.steps)`）。
续写序号是 `_execute` 另发的一条 `func.max(Step.seq)`。重试 3 次的 Run 每次重试都白读几十条含大字段的 step。

**修法**：删掉 `selectinload(Run.steps)`（连带 import），把 `max(Step.seq)` 合并进 `_load_context` 返回。

### P4【后端】`_persist_artifact` 在事件循环里同步上传 MinIO

`backend/app/services/sandbox_service.py:202-229` → `artifact_service.put_bytes`（同步 minio 客户端）

注意：11-17 审计 #6 修的是 **API 层 presign 路径**（`presign_*_async`），这条是
**沙箱产物落库路径，当时未覆盖**，不是回归。

`persist_sandbox_result` 由 `runner.on_sandbox_result` 在事件循环内 await。上传 N 个文件（累计上限 50MB）
期间事件循环完全停摆 → 取消探针 `cancellation_probe`（`result(timeout=2)`，超时即判定"已取消"）
可能误杀正在跑的沙箱。

**修法**：最小改动 `await asyncio.to_thread(_persist_artifact, ...)`。进一步可把
`payload["artifacts"]` 里的 `bytes` 换成 `SpooledTemporaryFile`，把峰值内存从 4 并发 × 50MB 降到常数
（`put_bytes` 已支持 `BinaryIO`）。

**本轮未修（保留）**：改成 `to_thread` 只需一行，但真正的问题是 `payload["artifacts"]`
里挂着 `bytes` 导致峰值内存 ×4 并发；只挪线程不改内存模型，等于把「阻塞事件循环」
换成「4 个线程同时各持 50MB」。要一次改对就得连 `SpooledTemporaryFile` 一起做，
而那会动到 `sandbox_service` / `artifact_service` / runner 三处的数据形状——
需要一轮真实压测确认峰值，不是顺手能收的。留待独立特性。

### P5【后端】每秒钟十余次 Docker API 往返

`backend/app/executors/sandbox_client.py:397`（`_ResourceSampler` 每 100ms 一次 `stats`）、
`:983-1013`（每轮 `reload()` + `exec_run()` 两次往返）、`:851` / `:1254`（20ms 轮询 `logs()`）

一次 60s 执行 ≈ 600 次 stats + 120 次 reload + 120 次 exec ≈ **840 次 daemon 往返**，
全部在 docker daemon 同一把锁上排队。

**修法**（原本写作「纯收益、无风险」，修复时复查后修正见下）：
- stats 采样降到 500ms~1s，或只在 `finish()` 前采一次（cgroup 的 `peak` 已覆盖 OOM/CPU 峰值）；
- 健康检查降到 3~5s（内核 lease ≤1s 过期，helper 死了下一轮照样抓得到），并把 reload+exec 合成一次；
- 就绪轮询改指数退避，或 `logs(follow=True)` 读到标记即停。

**本轮未修（保留）**：这三处频率都是**刻意的**——stats 100ms 是为了资源熔断能及时
掐断失控脚本，20ms 轮询 `logs()` 是为了取消探针能快速响应，reload+exec 是沙箱存活的
心跳。降频会直接拉长「取消/熔断生效」的延迟，这是安全属性换吞吐，不能凭「往返次数多」
就改。要降频得先有一份真实的「取消响应时间」基线，本轮没有。

### P6【前端】全局 `staleTime: 0`，字典数据每次挂载都重取　✅ 已修 `fd85bb4`

`frontend/src/App.tsx:16-20`

```ts
defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } }   // staleTime 缺省 = 0
```

受影响的常驻字典查询：

| key | 出现位置 |
|---|---|
| `['agents','active','all']` | BoardPage:152、GroupPage:161、CommandPalette:64 |
| `['groups','all']` | BoardPage:156、SchedulesPage:102、CommandPalette:70 |
| `['users','options']` | BoardPage:137、MemberSection:55 |
| `['projects','options','relevant']` | BoardPage:144、RequestForm:62 |

看板 ↔ Agent 页 ↔ 组页来回切换，每次重挂载都重拉一轮。而 `CommandPalette` 单独给
agents/groups 设了 `staleTime: 60_000`（:67/:73），`useToolsCatalog` 设了 60s —— 说明团队知道该设，
只是**最常驻的看板没设**。

另有一处已经跑偏：`SchedulesPage.tsx:97` 用 `['agents','list','active','all']`，与其他三处
`['agents','active','all']` 不是同一个 key → 调度页和看板各存一份。

**修法**：全局给 `staleTime: 30_000`（配了 `refetchInterval` 的实时查询不受影响），
并把字典 key 收进 `src/api/queryKeys.ts` 常量。

### P7【前端】分页列表翻页闪空、看板拖拽全量重算　✅ 已修 `fd85bb4`、`8a86d84`

- **翻页闪空**：6 个分页页（Admin/Agent/Group/Projects/Schedules/TriggerHistory）均未用
  `placeholderData: keepPreviousData`，翻页时列表先变骨架屏再填充。逐页加一个参数即可。
- **看板拖拽**：`components/board/Board.tsx:159-160` 的 `countOf` 每次 render 对每家需求扫一遍
  （4 个 chip + 5 列各一次 filter = 每条被扫 9 次）；全仓 **零 `React.memo`**
  （`grep` 结果为 0），`RequestCard` 的 `onOpen` 每次都是新箭头函数（:378），memo 机会全部浪费。
  拖拽本身是这页最核心的交互，需求上百时掉帧可感。

**修法**：计数收成一次遍历 + `useMemo`；分列结果 `useMemo`；`RequestCard` 套 `memo` 并把
`onOpenDetail` 直接传入（卡片内部 `useCallback`）。

---

## 三、重复与漂移（当前不出错，但下次改动会漏）

这一类的共同危害：**改一处不漏另一处**。都能量化。

### R1 归属判权被复制 10 处，且 admin 口径不一致 ✅ 已修（`9245073`）

| 谓词 | 处数 | 位置 |
|---|---|---|
| 需求 owner-or-assignee → 404 | 5 | `request_service:198`、`dispatch_service:43`、`dependency_service:103`、`api/runs.py:43`、`api/ws.py:83/111` |
| Agent 归属 → 404 | 5 | `agent_service:22`、`assignment_service:63`、`group_service:118`、`egress_service:42`、`request_service:406` |

**真实差异不是设计而是漂移**：`api/runs.py` 与 `api/ws.py` 放行 admin，`request_service._check_owner`
不放行；`egress_service`（:42）放行 admin，`agent_service` 不放行。也就是说**管理员能改任意 Agent 的
出站网络策略，却改不了它的名字**——这是权限面上的实际不一致。

而 `api/deps.py:66` 的 `get_owned_resource` 本来想当这个收敛点，注释里自己承认做不到，于是
生产代码零引用，只剩测试在用（死代码）。

**修法**：在 `request_service` / `agent_service` 各出一个返回 `bool` 的 policy 函数
（`can_view_request` / `load_agent_for(..., allow_admin=)` ），10 处改为调用；
`deps.get_owned_resource` 删掉或改成真正被使用的依赖工厂。

### R2 错误文案硬编码，且被字符串匹配依赖 ✅ 已修（`feb092a`）

```
"项目不存在"  13 处      "Agent 不存在" 12 处
"需求不存在"   6 处      "组不存在" / "调度不存在" / "用户不存在" 各 4 处
```

这不是风格问题：`schedule_service._translate_assign_failure`（:800-812）靠
`"归档" in message` / `"不存在" in message` 做失败原因归类，`dependency_service` 同样依赖文案精确值。
任何一处改字都会**静默**破坏归类，编译器和类型检查都发现不了。

**修法**：抽 `app/api/messages.py` 常量类；`_translate_assign_failure` 改为读 `exc.code` +
service 抛错时带 `detail={"reason": "archived"}`，彻底摆脱字符串匹配。

### R3 状态集合定义 6 份，其中一份混入了别的领域 ✅ 已修（`daed578`）

```python
dependency_service.py:31      TERMINAL_STATUSES = {"completed","failed","cancelled"}
dependency_graph_service:26   TERMINAL = {"completed","failed","cancelled"}
request_state_machine.py:41   _TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})
request_service.py:27         _ACTIVE_RUN_STATUSES = {"queued","running","needs_input"}
reaper_service.py:34          _ACTIVE_STATUSES = ("queued","running","needs_input")
schedule_service.py:51        _UNFINISHED_REQUEST_STATUSES = {"created","assigned","running"}
```

**已核实的实质问题**：`dependency_service.ready_dependents` 的 skip 集合（:308-314）里写了
`"timeout"`。但 `request_status` 枚举（`alembic/versions/0001:191-199` + `0011:172-176`）只有
`created/assigned/pending/accepted/running/completed/failed/cancelled` 八个值，**没有 `timeout`**
（`timeout` 是 `run_status` 的值）。`request_service.py:189` 的 `{"failed","timeout"}` 同理——
那一处是查 Run 状态，语义正确；但混在需求状态集合里就是领域串味。

新增一个需求状态（如 `paused`）时，6 个集合漏改 1 个的表现是「暂停的需求被当成终态解锁了后续」。

**修法**：两个 state_machine 各导出 `TERMINAL_REQUEST_STATUSES` / `ACTIVE_RUN_STATUSES` /
`TERMINAL_RUN_STATUSES`，其余模块引用；`ready_dependents` 的 skip 删掉 `timeout`。

### R4 三个超大函数，且内部有 7 份相同的「状态迁移 + 落库 + 发事件」三连 ✅ 已修

> 落地记录见文末「R4 落地记录」与「R4 过程中对本报告的四处修正」。
> 一处提法需先更正：报告说「三连被复制 7 次」，连 `execute_run` 一起实为 **13 处**
> （runner 7 处 + `_execute` 6 处）。

| 函数 | 行数 | 混了几件事 |
|---|---|---|
| `orchestrator/runner.py:217` `run()` | 535 | 取消/超时/max_steps 三种终止、上下文裁剪、LLM 调用与异常分类、成本熔断、step 持久化、工具分派、越权审计 |
| `tasks/execute_run.py:409` `_execute()` | 421 | 路由、配额、授权、沙箱回调、步骤续写、pipeline 推进、需求收敛 |
| `services/external_invocation_service.py:117` `build_external_executors` | 244 | 7 表 join、五组策略判定、凭据解密、transport 分支、4 个闭包 |

这三处的共同点不是"长"，而是**同一段三连被复制 7 次**：

```python
transition(run, RunStatus.FAILED); run.error = ...; await set_status(...); await publish_event(...)
```

（runner.py:288/318/341/660/688，execute_run.py:877/898/456）

复制已经产生了**语义不一致**：外部工具失败发 `run_failed`，而同样的沙箱 137 分支发
`sandbox_finished`；`cancelled` 分支有的发 `run_cancelled`、有的只发 `status_change`。
这类不一致靠读 535 行才找得到，测试也不会报。

**修法**（可分 PR 渐进，不必一次重构）：先抽 `_fail(run, msg, *, event=)` / `_cancel(run)`
两个终结器替换 7 处；再把工具分派整段抽成 `_execute_tool_calls(resp, ctx)`；
`_execute` 里的沙箱回调搬到 `tasks/sandbox_callbacks.py`。

### R5 小规模重复（顺手清理）✅ 已修（`23f8428`）

- **工具 schema 三处硬编码**：`read_url.py:140` / `calc.py:148` / `http.py:27` 各自手写一份，
  与 `catalog.py:57-115` 完全相同。`sandbox.py:163` 已示范正确做法（`CATALOG[self.name]`）。
  三者改为 `to_openai_schema(self.name)` 可删约 40×3 行与对应的"反向校验"测试。
- **`ProjectOut.model_validate(...).model_copy(update={...})` 出现 5 次**（`projects.py:48/90/139/183`），
  且「成员数含 owner」这个口径只在列表端点体现 → 抽 `ProjectOut.from_model(project, *, ...)`。
- **`is_builtin` / `visibility` 派生逻辑两份**（`agent_templates.py:38` vs `agents.py:304`）
  → 提到 `agent_template_service`。
- **前端「导入模板」逐字复制两遍**（`AgentTemplatesPage.tsx:264-288` 与 `:312-336`），
  含 `e.target.value = ''` 这类易漏规则 → 抽 `TemplateImportButton`。
  顺带：`className="hidden"` 让 `<input type="file">` 无法聚焦，键盘用户无法触发导入 → 改 `sr-only`。
- **死代码**：`hooks/useRequests.ts` 的 `useRequests`（:28）与 `useUnreadCount`（:150）零调用点，
  且 `useRequests` 的 key `['requests', scope]` 与看板实际用的 `['requests', scope, project]` **不同**
  —— 谁"顺手"换上去，项目筛选缓存立刻串台。直接删。
  后端同类：`egress_network.parse_conntrack_output`（:638）、`compile_iptables_commands`（:499）、
  `sandbox_service.collect_artifacts`（:296）均只被测试引用，且与运行时实现已分叉 →
  删掉或明确标 `# test-only`。

---

## 四、建议的执行顺序

按「收益 / 风险」排，前三步都是小改动、可独立验证。

| # | 做什么 | 改动量 | 为什么排这里 | 本轮 |
|---|---|---|---|---|
| 1 | **D1 + D2 + D3**（前端三个确定性 bug） | 各 1~5 行 | 都在核心交互路径上，用户可感知，修完即可回归验证 | ✅ |
| 2 | **D4 调度提交顺序** | 5 行 | 后果是永久静默失效，但修法极简 | ✅ |
| 3 | **P1 + P2 + P3**（后端 N+1） | 各 20 行内 | 往返次数可在测试里量化，回归风险低 | ✅ |
| 4 | **D5 + D6**（并发正确性） | 各 20~30 行 | 概率低但属"能造成生产事故"，复用已有 CAS 片段 | ✅ |
| 5 | **P6 + P7**（前端渲染与缓存） | ~100 行 | 一次性消除重复 key + 5 个页面翻页闪空 | ✅ |
| 6 | **R1 归属判权收敛** | 中等 | 先统一 admin 口径，否则后续任何权限特性都要改 10 遍 | ✅ |
| 7 | **R4 拆巨型函数** | 大（分 PR） | 先做机械化的终结器抽取，再做 DAG 无关的搬迁 | ✅ |
| 8 | P4 / P5 / R2 / R3 / R5 | 零散 | 随时可插空做 | ✅（R2/R3/R5）；P4/P5 保留见正文 |

第 6、7 步**已做完**（R1 见 `9245073`，R4 见下文 6 个提交）。原本排在这里的理由是
「重构不该和修 bug 混在同一批提交」——这一点仍然成立：它们全部在 `fix/audit-2026-11-21`
分支上单独成提交，每个都跑了全量门禁。

**两个观察项也已落地**（`dcff7be`），复查后确认它们都不是「等数据量大了再说」：
`ready_dependents` 的 N+1 会随租户数据总量线性劣化，`_adjacency_for` 的整表加载
在既有业务路径上就会被放大。已改为固定 2 次查询 + 只取相关边。

## 五、核对结论

- 代理报出的 4 条"高危"里，**3 条完全成立**（D1/D2/D3 前端 + D4 后端提交顺序），
  1 条（执行锁竞态）后果成立但**触发概率远低于最初描述**，本文已按实际概率标注为 D6。
- 代理称"MinIO 同步调用阻塞事件循环"是回归 —— **不成立**。11-17 审计 #6（`02cfa06`）修的是
  API 层 presign 路径，`sandbox_service` 这条产物落库路径当时未被覆盖，属于**新路径未纳入**。
  结论仍要修，但性质不同（不是回归）。
- 所有 `文件:行号` 均已回读源码确认，可直接跳转核验。

### 修复阶段对本报告的三处修正

修复过程本身又查出本报告写得不准的地方，如实记录：

1. **D5 定性过轻**。原文只说「并发首次访问泄漏子进程」，实际还有一个更致命的问题：
   `execute_run` 每次调用都起新事件循环，池条目绑在旧循环上，第二次工具调用必然抛
   `got Future attached to a different loop` —— 024 的常驻池在生产形态下第二步就崩。
   已在 `851e90a` 一并修复。
2. **P7 的「6 个分页页」是笔误**。实际适用 `placeholderData` 的是 5 个页面
   （Agent / Group / Projects / Schedules）+ TriggerHistory 弹层；**AdminPage 不在其中**——
   它是全仓唯一用裸 `useEffect` + fetch 的分页页，根本不经 TanStack Query。
3. **P5 的「纯收益、无风险」判断错误**。那些高频采样/轮询是刻意的：它们直接决定
   取消与熔断的响应延迟。降频是拿安全属性换吞吐，不是纯收益。已在正文改正并改为保留。

另：D4 的 `DependencyBlockedError` 分支在修复时被确认**当前不可达**——调度建的是全新需求，
创建时不可能已有依赖边（`RequestDependency` 只能经 `dependencies.py` 创建，已 grep 确认）。
该分支是防御性的，因此 D4 的实际影响面比原文描述小；但提交顺序的修法本身仍然正确
（换成「先写触发记录再提交」后，即便将来该分支变为可达也不会卡死）。

### R4 落地记录（6 个提交，逐条可查）

| 提交 | 做了什么 | 核心结论 |
|---|---|---|
| `e9793ae` | 终结器：`runner._settle` + `execute_run._fail_run`，替换 13 处三连 | `event_type` 逐处显式，不按 status 推导；`settle` 刻意不给默认值 |
| `ccaadf7` | 沙箱回调搬出 `_execute`（131 行 → `tasks/sandbox_callbacks.py`） | 搬迁前这两个回调**零直接测试**，补 11 条 |
| `619e25e` | 给 `build_external_executors` 补 23 条策略覆盖 | 拆之前先有覆盖，否则「行为不变」无从证明 |
| `42fdc29` | `build_external_executors` 244 → 6 个函数 | 判定顺序本身是契约 |
| `58873db` | 越权阈值熔断补 4 条覆盖 | 该分支此前**零覆盖**，是 `run()` 里唯一「主动放弃模型」的分支 |
| `1293ca1` | 工具分派抽成 `_execute_tool_calls`（`run()` 580 → 252 行） | 抽取时 ruff 报 `tools` 未使用，牵出一个**真实缺陷**（见下） |
| `3272f60` | 补齐第四处 schema 派生（`sandbox.py`）+ 一条能真正拦住手写的测试 | 「替换 CATALOG」拦不住手写，得替换拼装函数 |
| `47e1f0d` | `authorize_tool` 从 `_execute` 抽出 + 10 条授权复核覆盖 | 缺陷修复了，但「撤销怎么被发现」当时仍零覆盖 |

### R4 过程中对本报告的四处修正

1. **「`compile_iptables_commands` 与运行时实现已分叉」不成立**。两者共用
   `_compile_with_chains`，规则必然一致。真正的危险是**包装里独有的 bridge 格式校验
   运行时从没有过**——「测试比生产更严格」让人误以为有保护。已把校验移入
   `compile_runtime_commands`（`23f8428`）。
2. **「三处工具 schema 硬编码」实为四处**。`sandbox.py:163` 也手写，只是从
   `CATALOG[self.name]` 取值而不是写字面量。R5（`23f8428`）当时只改了 calc /
   read_url / http 三处，第四处留到 `9f0a4d1` 才补上——**报告先写了「四处」，
   代码只改了三处**，这个不一致本身是缺陷。
   另外「删掉两份比对测试」的判断也要改：schema 从 `to_openai_schema()` 派生后，
   「两份比对」在数学上恒真，必须换成「monkeypatch 替换 CATALOG，执行器 schema
   必须跟着变」才有信息量。
   还要再进一步：**连这条也不够**。手写拼装同样读 `CATALOG[self.name]`，改 catalog
   时它也会变，所以「替换 CATALOG 定义」拦不住手写（实测：把 `sandbox.schema()`
   改回手写，该条全绿）。真正能拦住的是「替换**拼装函数本身**，执行器必须跟着
   走」——风险在 `function` 的形状（将来加 `strict` 等字段），不在数据。
   两条测试分工见 `test_catalog.py` 文件头。
3. **「沙箱 137 分支发 `sandbox_finished` 与外部工具 `run_failed` 不一致」属层级错配**。
   沙箱失败只让**工具步**失败，Run 仍可继续；`sandbox_finished` 是步骤级事件，与
   Run 级 `run_failed` 不在同一层，对齐会让一次沙箱报错连带整个 Run 死掉。已在
   `test_sandbox_callbacks.py` 里把这条写成反向约束（`test_failed_sandbox_marks_step_failed_but_does_not_touch_the_run`）。
4. **`_execute` 的「421 行混七件事」里，沙箱回调占 131 行**，比预估的多。搬走后
   `_execute` 为 298 行（最初记的 289 是搬完当刻的数字，后来补 `_fail_run` 调用点
   与注释又涨了几行）；剩余部分是路由/配额/授权/续写/pipeline，彼此共享 DB 会话与
   `run` 对象，继续机械切分收益递减，就此打住。

### 抽取过程中新发现的一个真实缺陷（`1293ca1`）

`run()` 里的工具清单只在 `while` 循环**之前**算一次，而撤销授权那段重新算了一份
赋给循环内的局部变量——**下一轮 LLM 读的仍是循环外那份**。后果：管理员在 Run 执行
期间撤销某个工具后，执行器确实被摘掉（再调会被拒），但模型下一轮仍拿到旧清单、
继续尝试调用已禁用的工具，只能靠越权熔断兜底（默认要累积 5 次）。

`authorize_tool` 此前零测试。新增用例断言**第二次 LLM 调用收到的 `tools` 参数**——
正是原来漏掉的地方。反向验证：把修复改回写无人读的局部变量，测试立刻红。

不过「撤销被发现了」和「撤销是怎么被发现的」是两件事。前者在 runner 侧（`1293ca1`
的 `test_revoked_tool_disappears_from_the_next_llm_tool_list`），后者是 `_execute` 里
那段查库判定——当时仍是零覆盖。`47e1f0d` 把它抽成模块级
`_authorize_tool_now(db, *, name, agent, run, external_executors)` 并补 10 条，
把四条规则逐条钉住（快照 ∧ 当前、外部工具短路、无 Agent 拒绝、None 快照按无授权）。
三个方向的反向验证都先红后绿：只信快照红 3 条、只看当前红 2 条、去掉外部工具短路红 1 条。
