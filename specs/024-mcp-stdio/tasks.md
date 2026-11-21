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

## 未接线（明确不勾）
- [ ] T201 模型加 `transport` / `command` / `args`（`endpoint_url` 语义要重新设计）
- [ ] T202 `validate_server` 按 transport 分叉（stdio 用握手 + tools/list 探活）
- [ ] T203 `build_external_executors` 按 transport 分叉 + **进程生命周期决策**
- [ ] T204 前端 server 表单：transport 选择 + 命令/参数输入 + 白名单提示
- [ ] T205 真实栈验收（需要真实 npm/uvx 包或本地 fake server 作为已注册 server）

## 实测记录（2026-11-21）

| 门禁 | 结果 |
|---|---|
| 后端 | **2106 passed**（基线 2098，新增 8），覆盖率 **94.23%** |
| 新增测试 | `tests/unit/test_stdio_mcp_client.py` 8 例，全部**真实子进程**（非 mock） |
| 迁移 | 无（本轮不碰数据库） |
| 真实栈 | 不适用（未接入注册/调用链，见 spec 末节） |

8 例覆盖：白名单两种伪造、握手+列工具+调用、工具 isError、脚本不存在、
启动即崩（错误信息带 stderr）、关闭后无僵尸、stdout 非 JSON。
