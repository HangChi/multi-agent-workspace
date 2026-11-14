# Research: 核心工作流（Core Workflow）

**Phase 0 产出**：技术决策论证，全部 NEEDS CLARIFICATION 已消解（技术选型在架构文档与宪法已定，此处论证关键实现路径）。

## R-01 Celery 事件回调链路

**Decision**: Worker 状态变更通过 Redis Pub/Sub 发布事件 → API 进程订阅 → WebSocket 推送前端。

**Rationale**: API 为 uvicorn 多进程，Worker 为独立进程；Redis 是两者唯一共享组件，Pub/Sub 天然支持一对多广播，API 任一进程订阅同一频道即可覆盖全部前端连接。Worker 完成/失败/步骤推进时 publish，API 侧 async 订阅者转发给对应 run 的 WS 客户端。相比 Celery result backend 轮询（延迟高、耦合结果存储），事件总线实时性满足 SC-003（<2s）。

**Alternatives considered**:
- Celery result backend 轮询：实现简单但延迟不可控、每次查询打 Redis，不满足实时性。
- 直接 WS 从 Worker 连前端：Worker 无状态且可扩容，无法维护连接；跨进程会话共享复杂。
- 消息队列（RabbitMQ）：功能更强但引入新组件，单机部署复杂度上升，Redis Pub/Sub 已够用。

## R-02 Run 状态机的实现形态

**Decision**: 自研轻量状态机（显式状态枚举 + 允许迁移表 + 幂等迁移函数），不引入状态机框架。

**Rationale**: Run 生命周期（created→assigned→queued→running→completed/failed/cancelled/needs_input）迁移规则清晰、无并发分支复杂度，自研 50 行内可完成且完全可控；迁移函数校验合法迁移并更新时间戳，配合 Redis 分布式锁保证并发安全（FR-013 入队幂等）。

**Alternatives considered**:
- `transitions` / `xstate` 等框架：为复杂状态图设计，本项目状态图简单，引入依赖收益为负。
- DB 触发器约束状态：逻辑分散，调试困难。

## R-03 单 Agent ReAct 循环的执行模型

**Decision**: runner 以「消息序列 + 工具 schema」驱动 LiteLLM 循环：LLM 返回 tool_call → 执行器执行 → 结果回填 → 再调 LLM，直到最终答案或达 max_steps/超时/成本上限。

**Rationale**: 满足 FR-014（理解需求→调工具→产出结果）、FR-019（成本实时累计）、FR-015（每步可记录可推送）。每步写入 steps 表并发布事件，天然支撑实时日志与制品收集。循环在 Celery task 内运行（Worker 进程），单 run 一个 task，不阻塞 API。

**Alternatives considered**:
- 单次 LLM 调用直接出结果：无法满足「调用工具」与「分步可见」需求（FR-014/FR-015）。
- LangGraph 图执行：框架锁死、偏对话流，宪法明确禁用。
- Agent SDK（OpenAI/Claude Agent）：厂商锁定，LiteLLM 多模型切换被破坏。

## R-04 成本熔断机制

**Decision**: 每次 LLM 调用后累计 run_cost（从 LiteLLM 用量响应解析），写入 runs 表；runner 每步检查 `run_cost > agent.cost_limit` 即终止并标记失败。

**Rationale**: FR-019 要求成本不超上限。LiteLLM 统一返回 usage 结构（prompt/completion tokens），按模型单价换算成本（模型→单价表可配置），实时累计。检查放在每步循环内，实现简单且无竞态（单 worker 单 run 串行）。

**Alternatives considered**:
- 事后账单核对：违反「实时累计并展示」（FR-019 明确实时）。
- 独立成本监控服务：过度设计，单 run 成本累计在 runner 内天然串行。

## R-05 附件/制品存储

**Decision**: MinIO（S3 兼容）存储，API 生成预签名 URL 供上传/下载。

**Rationale**: 附件（FR-008 需求附件）与制品（FR-016 产出下载）均为二进制文件，不适合 DB。MinIO S3 协议与云对象存储（COS/OSS）兼容，后期可平滑迁移。预签名 URL 避免大文件经过 API 进程，减轻 API 负担（对齐「API 只入队不干活」）。

**Alternatives considered**:
- 本地磁盘 + Nginx 静态服务：无法横向扩展、无对象生命周期管理。
- 直接 Postgres BYTEA：大文件拖垮 DB 性能。

## R-06 前端拖拽交互方案

**Decision**: dnd-kit（useDraggable 需求卡 + useDroppable Agent/组卡），拖放后弹确认模态框（选策略），确认后调用分配 API。

**Rationale**: dnd-kit 是 React 生态最成熟的拖拽库，支持看板拖放、触摸屏、无障碍，满足 US4 核心交互。拖放仅前端交互，确认后走 API，职责清晰。

**Alternatives considered**:
- react-dnd：HTML5 拖放 API，触摸支持差、API 陈旧。
- 自研拖拽：成本高、可访问性难保障。
- 拖放即分配（无确认框）：误操作无法挽回，违反「拖到组需选策略」的 FR-011。

## R-07 实时日志推送的实现

**Decision**: WebSocket 端点 `WS /ws/runs/{id}`，API 进程订阅 Redis 频道 `run:{id}`，收到事件（status_change/step_new/log_line/cost_update）即推送给该连接的客户端；前端日志流节流合并（200ms 批量渲染）。

**Rationale**: 满足 SC-003（日志延迟 <2s）与 FR-015。Redis Pub/Sub 按 run id 隔离频道，多进程 API 都能订阅，前端重连自动恢复（重新订阅 + 拉取历史步骤）。

**Alternatives considered**:
- SSE：单向适合，但需额外心跳机制，且 WS 后续需要客户端发消息（补充信息），WS 更通用。
- 轮询：违反 SC-003 延迟要求。
