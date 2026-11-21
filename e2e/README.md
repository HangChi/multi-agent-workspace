# 常驻浏览器 e2e 套件

真实 Chromium 驱动的回归套件：**前端改动后跑一遍，替代「改 UI 就重写一个一次性脚本」**。

## 与 `deploy/verify/s12_browser_walkthrough.py` 的分工

| | s12 | 本目录 |
|---|---|---|
| 定位 | 一次性清单验收（008/009/010 三份 quickstart 的 15 项，出证据） | 可重复回归（按功能分文件，持续加用例） |
| 组织 | 单文件 46KB，A/B/C 三组 | `tests/` 每文件一个领域 |
| 登录 | 每条断言自己走 | 会话级 UI 登录一次，存 `storage_state` 复用 |
| 失败 | 截图 + 页面文本 | 同上（`e2e/.artifacts/`） |
| 何时跑 | 某个特性的验收阶段 | 每次前端改动 / 发版前 |

s12 **不删除**：它承载 011 的历史验收证据链。

## 跑法

```bash
# 1. 起全栈（推荐；前端与 API 同源在 8080）
docker compose -p aw -f deploy/docker-compose.yml up -d

# 2. 取一个可用邀请码（注册必须邀请码）
docker exec aw-postgres psql -U aw -d agent_workbench \
  -c "select code from invite_codes where revoked_at is null and used_count < max_uses limit 1;"

# 3. 跑
cd e2e
AW_E2E_INVITE=<上一步的码> ../backend/.venv/bin/python -m pytest -q
```

本地单独调一条用例、想看见浏览器：

```bash
AW_E2E_INVITE=... AW_E2E_HEADED=1 \
  ../backend/.venv/bin/python -m pytest tests/test_projects.py -q -k rename
```

没起栈时**不会红**：`base_url` 探测 `/api/v1/health` 失败、或没给邀请码，全部 `skip` 并打印原因。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `AW_E2E_BASE` | `http://127.0.0.1:8080` | 前端与 API 的同源地址 |
| `AW_E2E_INVITE` | 空 | 注册用邀请码；缺失即全 skip |
| `AW_E2E_HEADED` | 空 | `1` = 开窗（调 locator 时用） |
| `AW_E2E_CHROMIUM_PATH` / `S12_CHROMIUM_PATH` | 自动扫描缓存 | 指定浏览器可执行文件 |

浏览器解析顺序：显式 `*_CHROMIUM_PATH` → `PLAYWRIGHT_BROWSERS_PATH` →
`~/Library/Caches/ms-playwright`（mac）/ `~/.cache/ms-playwright`（linux），
版本目录倒序取第一个存在的（缓存里常有多个旧版本）。

## 目录

```
e2e/
├── conftest.py          # 夹具：栈探测、账号、登录态、失败留证
├── pytest.ini
├── support/
│   ├── api.py           # 数据一律经 API 造（断言失败时分得清是后端还是页面）
│   ├── browser.py       # 浏览器定位与启动参数
│   └── ui.py            # 页面操作辅助（两段式详情、关弹窗、铃铛）
├── tests/
│   ├── test_smoke.py        # 登录、五个主导航、设置二级页
│   ├── test_projects.py     # 内联新建/改名、成员共享可见
│   ├── test_requests.py     # 建需求 + 项目归属、看板 project 深链筛选
│   └── test_dependencies.py # 前置需求区块、阻塞提示、移除后刷新
└── .artifacts/          # 失败截图与页面文本（不入库）
```

## 加用例的三条约定

1. **数据经 API 造，浏览器只断言显示。** 这样失败原因不会含糊：
   接口断言红了是后端行为变了，页面断言红了是渲染/交互坏了。
2. **详情类断言要等两段式拉取。** 详情弹层先用列表数据秒开，再 `GET /requests/{id}`
   补全依赖与经手链；不等那次响应就断言会读到 `dependencies=null`
   ——用 `support/ui.open_request_detail(page, title, id)`，别自己 `click` 完就断言。
3. **`<select>` 用 `value=` 选，不要用 `label=`。** 选项文本带计数
   （`项目名（3）`），label 精确匹配必然失败，而 Playwright 对这种失败报的是
   「waiting for element to be visible and enabled」，容易误判成元素被遮挡。

## 当前覆盖边界（有意留白）

- **不跑真实 LLM 执行**：跑一次 run 要消耗 token 且外部依赖不稳定，
  「运行 → 日志 → 制品」仍由 `deploy/verify/s12_browser_walkthrough.py` 与
  后端集成测试覆盖。
- **拖拽分配**（dnd-kit 键盘/指针双传感器）不在本套件内，仍在 s12 的 A4/B4。
- viewer 角色门（013 的 C5）需要第二个邀请码（viewer 角色），暂由 s12 覆盖。
