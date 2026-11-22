# Tasks: MCP stdio 传输（024）

## 规格
- [X] T001 spec.md（问题 / **交付范围表** / 三条安全底线 / 协议要点 / 未接线四处）
- [X] T002 research.md（R-01~R-06）
- [X] T003 tasks.md

## 实现（协议与进程层）
- [X] T101 `StdioMCPClient`：exec 启动（不走 shell）+ 环境白名单 + 白名单命令校验
- [X] T102 换行分帧读取（跳过 notification、id 校验、大小上限）
- [X] T103 `initialize`（含 `notifications/initialized`）/ `list_tools` / `call_tool` / `cancel`
- [X] T104 生命周期：stdin 关闭 → terminate → 超时 kill → `await wait()` 回收
- [X] T105 stderr 持续读并保留尾部（崩溃信息里带上）
- [X] T106 `tests/fixtures/fake_mcp_stdio_server.py`（真实子进程夹具，支持 --crash）
- [X] T107 测试 8 例（白名单两种伪造 / 握手列工具调用 / 工具错误 / 脚本不存在 /
      崩溃带 stderr / 无僵尸 / 非 JSON 输出）
- [X] T108 门禁：ruff + format + mypy + pytest

## 接线（后续轮次补齐，`c88671a` + `c3369d5`）

> 首轮 `420f71b` 只交付协议与进程层，这节当时整段标成「未接线，明确不勾」。
> 第二轮已全部接上，但**忘了回勾**——这是本项目第三次同类疏漏
> （022 被 027 覆盖、022 的 5 条实际全实现、以及本次）。

- [X] T201 模型加 `transport` / `command` / `args` / `env_keys`
      （`endpoint_url` 保持非空，stdio 存 `stdio://<command>` 占位）
      → `backend/app/models/external_tool.py:44-52`
- [X] T202 `validate_server` 按 transport 分叉（stdio 用**一次性**子进程握手 +
      `tools/list` 探活，不进池）
      → `backend/app/services/external_validation_service.py:112`
- [X] T203 `build_external_executors` 按 transport 分叉 + 进程生命周期决策
      （**常驻池** + 每进程一把 `asyncio.Lock` + 出错标记 broken 移出池 +
      空闲 5 分钟下次 acquire 时重建；不做后台定时器）
      → `backend/app/services/external_invocation_service.py:209`、
      `backend/app/executors/stdio_pool.py`（`build_stdio_client_factory`）
- [X] T204 ~~前端 server 表单：transport 选择~~ —— **前提不成立**：前端自 003 起
      就没有「创建外部 server」的表单（创建走 API / 运维动作），UI 只有
      列表 / 审核 / 凭证 / 授权。改为列表上的 transport 徽章 + stdio 命令展示
      （排查路径两者完全不同，一眼要能分辨）
      → `frontend/src/components/admin/ExternalToolServers.tsx:191-199`
- [X] T205 真实栈验收 → `deploy/verify/s20_stdio_transport.py` **3/3**
      （创建 → worker 探活 `connection_status=passed` / `discovered_count=1`
      （2 秒）→ 非白名单与半配置 422）

## 实测记录

### 首轮（2026-11-21 上半场，协议与进程层）

| 门禁 | 结果 |
|---|---|
| 后端 | **2106 passed**（基线 2098，新增 8），覆盖率 **94.23%** |
| 新增测试 | `tests/unit/test_stdio_mcp_client.py` 8 例，全部**真实子进程**（非 mock） |
| 迁移 | 无（本轮不碰数据库） |
| 真实栈 | 不适用（本轮未接进注册/调用链） |

8 例覆盖：白名单两种伪造、握手+列工具+调用、工具 isError、脚本不存在、
启动即崩（错误信息带 stderr）、关闭后无僵尸、stdout 非 JSON。

### 第二轮（2026-11-21 下半场，接线）

| 门禁 | 结果 |
|---|---|
| 后端 | **2115 passed**（首轮 2106，新增 9），覆盖率 **94.17%** |
| 新增测试 | `tests/integration/test_stdio_transport.py` 6 例（创建校验 5 + 校验分叉 1）、`tests/unit/test_stdio_pool.py` 3 例（复用 / 串行 / 崩溃重建） |
| 迁移 | `0023 → 0025`（0024 无 DDL），真实库执行成功 |
| 真实栈 s20 | **3/3** |

## 明确没做的（引用 spec 末节）

- 真实第三方包（`npx -y @scope/server`）端到端——验收用本地 fake server，
  它证明「协议 + 进程 + 链路」，不证明某个具体 npm 包能跑；
- stdio server 的**总并发额度**：同一 server 串行是设计，多 server 之间的总并发
  靠 executor 层的并发槽，没有独立配额。
