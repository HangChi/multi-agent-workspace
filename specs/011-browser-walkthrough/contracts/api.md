# API Contract: 011 浏览器走查验收

本特性不新增/不改任何 API 端点——它**消费**既有端点。契约内容是
「脚本如何驱动既有 API 与 UI」。

## 1. CLI 契约（与 s1–s11 同构）

```
backend/.venv/bin/python deploy/verify/s12_browser_walkthrough.py \
  --base http://localhost:8080 \
  --invite <MEMBER-INVITE> \
  [--viewer-invite <VIEWER-INVITE>] \
  [--frontend-port 5199] \
  [--frontend-url http://localhost:5173]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--base` | `http://localhost:8080` | 后端 API（同 s 系列） |
| `--invite` | 必填 | member 邀请码（注册种子用户） |
| `--viewer-invite` | 无 | viewer 邀请码；不提供则 A5/C5 标记「跳过」并**不勾选** quickstart 条目 |
| `--frontend-port` | 5199 | 自管理 dev server 的端口（占用则明确报错退出） |
| `--frontend-url` | 无 | 指向已在运行的前端实例；提供则脚本不拉 dev server |

环境变量：`S12_CHROMIUM_PATH`（浏览器二进制直接指定）、
`PLAYWRIGHT_BROWSERS_PATH`（缓存目录扫描，默认 `~/Library/Caches/ms-playwright`）。

## 2. 种子 API 调用序列（顺序即依赖）

```
for who in (owner, mate, stranger[, viewer]):
    POST /api/v1/auth/register {email: w-<who>-<rand>@t.com, password, invite_code}
agent = POST /api/v1/agents (owner)
proj_a = POST /api/v1/projects (owner)          # A 组管理对象
proj_c = POST /api/v1/projects (owner)          # C 组共享对象
req_p1  = POST /api/v1/requests (owner, project_id=proj_a)
req_p2  = POST /api/v1/requests (owner)          # 无归属（A4 徽章阴性对照）
req_b   = POST /api/v1/requests (owner)          # B 组后续方
edge    = POST /api/v1/requests/{req_b}/dependencies {dependency_id: req_p1}   # B 组阻塞边
member  = POST /api/v1/projects/{proj_c}/members {user_id: mate}              # C 组成员
```

## 3. UI 驱动契约（按清单编号）

### A 组（008）

| # | 起点 | 操作 | 断言 |
|---|---|---|---|
| A1 | `/projects` | 看列表字段 → 点新建 → 填名 → 保存；点行内改名 → 改值 → 保存；点归档；点删除（确认弹窗） | 列表含名称/状态徽章/需求数；新建项出现；改名后标题更新；归档徽章变「已归档」；删除弹窗文案含「保留」 |
| A2 | `/` 点「新建需求」 | 打开表单看项目选择器默认值；展开下拉看选项 | 默认「不归类」；下拉仅本人 active 项目（无归档项/无他人项） |
| A3 | `/` | 看工具条项目下拉选项；选某项目；看 URL；刷新 | 含「全部项目/各项目（计数）/未归属」；URL 出现 `?project=<id>`；刷新后下拉值保持 |
| A4 | `/` | 看 req_p1 卡片与 req_p2 卡片 | req_p1 有项目徽章（项目名）；req_p2 无徽章 |
| A5 | viewer 登录后 | 看工具条与导航 | 无项目下拉；导航无「项目」入口 |

### B 组（009）

| # | 起点 | 操作 | 断言 |
|---|---|---|---|
| B1 | `/` 点 req_b 卡片详情 | 看依赖区块；用添加器连一条；点移除 | 边显示（标题+状态徽章）；连后列表 +1；删后列表 -1 |
| B2 | 同上（有未完成前置） | 看区块顶部 | 文案「有 N 个前置未完成，暂不能分配执行」 |
| B3 | `/` | 看 req_b 卡片；hover 徽章 | 「N 个前置未完成」+ hover 含前置标题 |
| B4 | `/` | 拖 req_b 到侧栏 Agent 目标（mouse 序列） | 错误横幅含前置标题（逐条）；传感器不响应则退化点选并注明 |
| B5 | 前置终态后看铃铛 | 点通知 | 「前置就绪」通知存在；点击跳到后续需求详情 |

### C 组（010）

| # | 起点 | 操作 | 断言 |
|---|---|---|---|
| C1 | owner `/projects` | 看 proj_c 成员区；用添加器加 stranger；看列表 | 创建者徽章 + 成员列表 + 添加器/移除；加后列表更新 |
| C2 | mate 登录 `/projects` | 看 proj_c 卡片与成员区 | 「共享的」徽章；成员区无添加器/移除（只读提示） |
| C3 | mate `/` | 看项目下拉；开新建需求表单 | 下拉含 proj_c；表单项目选择器含 proj_c |
| C4 | mate 铃铛 | 看通知 | 「你已被加入项目 X」存在 |
| C5 | viewer 登录 | 看导航与项目页 | 无项目入口；无成员操作 |

## 4. 输出格式（与 s 系列一致）

```
✅ A1 管理页 CRUD — 列表字段/新建/改名/归档/删除确认（0.8s）
❌ A3 项目下拉与深链 — URL 未出现 ?project=...（1.2s）
   产物：deploy/verify/.artifacts/s12-<ts>/A3-*.png
...
合计 15 项：14 通过 / 1 失败
```

- 退出码：全过 0；有失败（含跳过项？——**跳过不算失败**，单独计数）
  1。汇总行格式「合计 15 项：N 通过 / M 失败 / K 跳过」。

## 5. 错误码速查（脚本自身）

| 情况 | 行为 |
|---|---|
| `--base` 不可达 | 首项即失败，输出连接错误（不重试） |
| dev server 端口占用 | 明确报错退出（不换端口） |
| 浏览器解析失败 | 明确退出 + `S12_CHROMIUM_PATH` 提示 |
| 某项 UI 断言失败 | 该项 ❌ + 产物；后续项继续跑（不中断——一次跑拿全失败面） |
| viewer 邀请码缺失 | A5/C5 标记「跳过」，不计失败 |
