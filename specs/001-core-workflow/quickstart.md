# Quickstart: 核心工作流（Core Workflow）

**Phase 1 产出**：可执行验证场景，证明特性端到端可用。对齐 spec 的 Acceptance Scenarios 与 SC。

## 前置条件

- Docker + Docker Compose 已安装。
- `.env` 已配置（`cp deploy/.env.example deploy/.env`）：`DATABASE_URL`、`REDIS_URL`、`MINIO_*`、`JWT_SECRET`、`LLM_API_KEY`（LiteLLM 网关）、`INVITE_CODE`（首个管理员邀请码）。
- 启动：`docker compose -p aw -f deploy/docker-compose.yml --profile mock up -d --build`（`-p aw` 固定项目名；`--profile mock` 拉起 Mock 网关，生产部署去掉）。
- 首个管理员创建：通过 admin CLI 或脚本生成邀请码并注册 admin（见实现阶段细节）。
- **离线复现 S5**：compose 内置 `llm-gateway`（`deploy/mock-llm/`）作为 OpenAI 兼容 Mock 网关，`LLM_BASE_URL` 指向它即可无真实密钥跑通执行链路；接入真实模型时替换为 LiteLLM 网关地址与密钥。

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
| SC-001 注册登录 <2min | S1 手动/API | ✅ 真实栈注册即自动登录 0.18s（`s1_s4_scenarios.py`）；集成测试 T019/T023/T028 通过 |
| SC-002 拖拽受理 <3s | S4 前端计时 | ✅ 真实栈受理 **12ms**（脚本实测）；集成测试断言 <3s |
| SC-003 日志延迟 <2s | S5 WS 计时 | ✅ 真实栈 WS 事件流完整（status/step/log/cost/completed），推送滞后 **-0.25s** |
| SC-004 简单执行 <5min | S5 计时 | ✅ 真实栈 ReAct 两轮闭环 **0.33s**（steps=llm→tool→llm→final） |
| SC-005 无权限 100% 拒绝 | S1/S2 403 断言 | ✅ 真实栈 未认证 401 / viewer 建 Agent 403 / member 访问审计 403 / 跨用户 403 |
| SC-006 成本不超上限 | S5 成本断言 | ✅ 真实栈 上限 1 分、累计 2 分即熔断终止（status=failed + 可读原因） |
| SC-007 20+ 并发不卡顿 | 压测（并发创建+分配） | ✅ 真实栈 20 并发分配→执行→完成 **1.71s**，全部 completed |
| SC-008 重试成功率 ≥80% | S5 重试断言 | ✅ 真实栈 造失败 5/5 → retry 受理 5/5 → 恢复 **5/5（100%）** |

## 验证记录表

| 场景 | 结果 | 日期 | 备注 |
|---|---|---|---|
| S1 注册登录与权限 | ✅ 通过 | 2026-11-14 | 真实栈 8 项断言全过（注册自动登录/登录/refresh/密码错 403/无效码 400/未认证 401/两种角色 403）；集成测试 12+11 全绿 |
| S2 Agent 与组管理 | ✅ 通过 | 2026-11-14 | 真实栈 7 项断言全过（创建/编辑生效/非 owner 403/组+成员+策略持久化/重复成员 409/组引用仅归档/归档不进 active） |
| S3 需求与看板 | ✅ 通过 | 2026-11-14 | 真实栈 7 项断言全过（创建 201/附件预签名 PUT 200 + 下载 302 内容一致/元数据回填/状态分组/关键词命中/超限与类型 400/非 owner 403） |
| S4 拖拽分配 | ✅ 通过 | 2026-11-14 | 真实栈 7 项断言全过（direct 202+running/SC-002 12ms/fan_out 生成 2 子 Run/重复 409/归档 400/不存在 404/非 owner 403） |
| S5 执行与实时反馈 | ✅ 通过 | 2026-11-14 | 真实栈 12 项断言全过（简单执行/失败→可读错误→重试/执行中取消/挂起取消/成本熔断/needs_input→reply→完成/WS 事件/20 并发/重试成功率 100%） |
| 前端交互（S4/S5 浏览器） | ✅ 通过 | 2026-11-14 | Playwright 真机 Chrome 9 项全过：登录→看板渲染→**鼠标拖拽卡片到 Agent → 确认弹窗（含目标名/优先级）→ 确认分配 → 卡片转「执行中」+出现「查看运行」→ 运行详情 WS「已连接」→ 状态到「已完成」、成本 ¥0.04、4 个步骤全 completed、当前步骤=4；无 console error** |

> 全部 41 项真实栈断言（S1~S4 共 29 + S5 共 12）+ 9 项浏览器断言均打真实容器栈，
> 与进程内集成测试互补。**未实际执行的验证绝不标记为通过**（工作流纪律）。

### 浏览器验证中发现并修复的缺陷

| 缺陷 | 现象 | 根因 | 修复 |
|---|---|---|---|
| 前端零样式 | 整站无 CSS，元素堆叠成纯文本流 | `frontend/postcss.config.js` 缺失，`@tailwind` 指令未处理（CSS 产物仅 57 字节） | 补 PostCSS 配置（tailwindcss + autoprefixer），产物 21.6 KB |
| api 重启后前端 502 | 重建 api 容器后 `/api/*` 持续 502，须重启前端才恢复 | nginx 启动时静态解析 `api:8000`，容器换 IP 后仍打旧地址 | nginx 改为变量式 `proxy_pass` + Docker 内嵌 DNS `resolver 127.0.0.11 valid=10s` |
| 「当前步骤」恒为 0 | 运行详情已执行 4 步，字段仍显示 0 | `Run.current_step` 从未被写（仅复位时置 0） | `save_step` 回调内推进 `current_step` 并落库，集成测试加断言 |

## 复现方式（真实栈验证脚本）

```bash
# 全套服务（含 mock LLM 网关，用于离线复现 S5）；生产部署去掉 --profile mock
docker compose -p aw -f deploy/docker-compose.yml --profile mock up -d --build

# S1~S4：需要 member/viewer 邀请码（首次执行时插入）
docker exec aw-postgres psql -U aw -d agent_workbench -c "
INSERT INTO invite_codes (id, code, role, max_uses, used_count, created_at) VALUES
  (gen_random_uuid(),'VERIFY-MEMBER-A','member'::invite_role,100,0,now()),
  (gen_random_uuid(),'VERIFY-MEMBER-B','member'::invite_role,100,0,now())
ON CONFLICT (code) DO NOTHING;"

backend/.venv/bin/python deploy/verify/s1_s4_scenarios.py --base http://localhost:8080
backend/.venv/bin/python deploy/verify/s5_scenarios.py --base http://localhost:8080 \
  --invite TEST-ADMIN-CODE-123

# 前端交互（S4 拖拽 / S5 实时反馈）：真实 Chrome，截图落在 deploy/verify/artifacts/
NODE_PATH=$HOME/.workbuddy/binaries/node/workspace/node_modules \
  node deploy/verify/ui_s4_s5.cjs
```

> `deploy/mock-llm/` 为验证专用 OpenAI 兼容网关（关键词路由 FAIL / 补充信息 / 正常两轮 ReAct），
> 生产部署请替换为真实 LiteLLM 网关，该目录不会打进后端镜像。
