# Quickstart: 核心工作流（Core Workflow）

**Phase 1 产出**：可执行验证场景，证明特性端到端可用。对齐 spec 的 Acceptance Scenarios 与 SC。

## 前置条件

- Docker + Docker Compose 已安装。
- `.env` 已配置（复制 `deploy/.env.example`）：`DATABASE_URL`、`REDIS_URL`、`MINIO_*`、`JWT_SECRET`、`LLM_API_KEY`（LiteLLM 网关）、`INVITE_CODE`（首个管理员邀请码）。
- 启动：`docker compose -f deploy/docker-compose.yml up -d --build`。
- 首个管理员创建：通过 admin CLI 或脚本生成邀请码并注册 admin（见实现阶段细节）。

## 验证场景（对齐 Acceptance Scenarios）

### S1 注册登录与权限（US1 / FR-001~003 / SC-001, SC-005）

1. 用管理员邀请码注册新用户 → 预期：注册成功自动登录，进入工作台。
2. 登出后用凭证登录 → 预期：登录成功。
3. 创建 viewer 角色用户，登录后用其会话尝试创建 Agent → 预期：403 且提示无权限。
4. member 访问管理后台 → 预期：403。

**命令参考**：
```bash
curl -X POST http://localhost/api/v1/auth/register -H 'Content-Type: application/json' \
  -d '{"email":"new@x.com","password":"Passw0rd!","invite_code":"ADMIN-CODE"}'
```

### S2 Agent 与组管理（US2 / FR-004~007）

1. member 创建 Agent（含提示词/模型/工具/成本上限）→ 预期：201，出现在列表。
2. 编辑该 Agent 提示词与成本上限 → 预期：200，修改生效。
3. 创建组，添加两个 Agent 排序，选「fan_out」策略 → 预期：201，成员与策略保存。
4. 用户 B 尝试编辑用户 A 的 Agent → 预期：403。

### S3 需求与看板（US3 / FR-008~009）

1. 创建需求（标题/描述/优先级）→ 预期：201，看板「待分配」列出现。
2. 上传附件 → 预期：201，详情可下载。
3. 看板按状态分组展示 → 预期：多状态正确分组。
4. 关键词搜索 → 预期：仅返回匹配需求。

### S4 拖拽分配（US4 / FR-010~013 / SC-002）

1. 前端把需求拖到 Agent → 确认 → 预期：202 `{"run_id","status":"queued"}`，卡片变「执行中」。
2. 拖到组选 fan_out → 预期：按策略生成运行，状态变「执行中」。
3. 对已执行需求再次拖拽 → 预期：409。
4. 拖到归档 Agent → 预期：400/404 并提示原因。

**前端手动验证**：工作台页拖拽需求卡到 Agent/组卡，观察确认弹窗与卡片状态变化。

### S5 执行与实时反馈（US5 / FR-014~021 / SC-003~004, SC-006~008）

1. 打开运行详情 → 预期：状态实时流转（排队→执行→完成），日志 <2s 延迟推送。
2. 完成 → 预期：展示最终结果与制品，可下载。
3. 制造失败（如 LLM 服务不可用）→ 预期：可读错误 + 一键重试，重试成功。
4. 执行中取消 → 预期：状态变「已取消」，停止执行。
5. 成本实时累计 → 预期：成本展示持续更新且不超 Agent 上限（超限自动终止）。
6. Agent 需要补充信息 → 预期：进入「等待补充」，回复后继续。

**命令参考**：
```bash
# 分配（拖拽的 API 等价）
curl -X POST http://localhost/api/v1/requests/{id}/assign -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"assignee_type":"agent","assignee_id":"{agent_id}"}'
# 查看运行
curl http://localhost/api/v1/runs/{run_id} -H "Authorization: Bearer $TOKEN"
# WS 订阅（浏览器或 wscat）
wscat -c "ws://localhost/api/v1/ws/runs/{run_id}?token=$TOKEN"
```

## 验收对照（SC → 验证方式）

| SC | 验证方式 | 状态 |
|---|---|---|
| SC-001 注册登录 <2min | S1 手动/API | ✅ T019/T023/T028 集成测试通过 |
| SC-002 拖拽受理 <3s | S4 前端计时 | ⏳ |
| SC-003 日志延迟 <2s | S5 WS 计时 | ⏳ |
| SC-004 简单执行 <5min | S5 计时 | ⏳ |
| SC-005 无权限 100% 拒绝 | S1/S2 403 断言 | ✅ T020/T028 集成测试通过 |
| SC-006 成本不超上限 | S5 成本断言 | ⏳ |
| SC-007 20+ 并发不卡顿 | 压测（并发创建+分配） | ⏳ |
| SC-008 重试成功率 ≥80% | S5 重试断言 | ⏳ |

## 验证记录表

| 场景 | 结果 | 日期 | 备注 |
|---|---|---|---|
| S1 注册登录与权限 | ✅ 通过 | 2026-11-12 | 集成测试 12+11 全绿（T019/T020/T028） |
| S2 Agent 与组管理 | ✅ 通过 | 2026-11-12 | 集成测试 24 全绿（T029/T030），前端 lint/typecheck/build 通过 |
| S3 需求与看板 | ✅ 通过 | 2026-11-12 | 集成测试 17 全绿（T041/T042，含预签名 PUT/GET 实测），前端看板+表单+附件上传构建通过 |
| S4 拖拽分配 | ⏳ | - | 实现未开始 |
| S5 执行与实时反馈 | ⏳ | - | 实现未开始 |

> S1/S2/S3 已通过（2026-11-12，集成测试验证）；S4–S5 仍标 ⏳：未实际执行的验证绝不标记为通过（工作流纪律）。
