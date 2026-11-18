# Implementation Plan: 011 浏览器走查验收

**Date**: 2026-11-18 | **Branch**: `feat/011-browser-walkthrough` | **Spec**: `specs/011-browser-walkthrough/spec.md`

## Technical Context

- 验收脚本：Python 3.13（backend venv）+ Playwright 1.65（sync API，dev extras）+ httpx（种子数据）
- 前端：React + Vite dev server（脚本以子进程拉起，Node ≥18）
- 新增依赖：**仅 dev extras**（`playwright>=1.40`，Docker `--no-dev` 不装，R-02）
- 安装注意：沙箱直连 pythonhosted 502，需 `HTTPS_PROXY=http://127.0.0.1:7897`（用户 2026-11-18 提供）
- 涉及既有文件：**零应用代码改动**（R-08）；仅新增 `deploy/verify/s12_browser_walkthrough.py`、
  `.gitignore` 一行、三份 quickstart 回填、README 登记

## Constitution Check

| # | 宪法原则（v1.1.0） | 符合性 | 结论 |
|---|---|---|---|
| 1 | I. SDD+TDD：先 spec 再实现 | ✅ | 九件套先于脚本 |
| 2 | I. Red-Green-Refactor 强制 | ✅ | P2 先写脚本跑出红（清单未验），实测转绿后才回填 quickstart |
| 3 | I. 测试未过不标记完成 | ✅ | FR-008 把「回填」绑定在「跑过」上；跳过项不勾 |
| 4 | II. spec 先行，四步走 | ✅ | 本目录九件套 |
| 5 | II. FR 必有 Given/When/Then 锚点 | ✅ | FR-001~010 挂 US 场景；contracts §3 逐项可执行 |
| 6 | II. 范围显式写「不含」 | ✅ | FR-009 + data-model §4 五条不做 |
| 7 | III. 完整系统非 MVP | ✅ | 15 项全覆盖 + 自管理 dev server + 产物 + 回填端到端 |
| 8 | IV. 每 Phase 质量门禁 | ✅ | P2/P3 均跑 s12 全过；P4 全量回归；P5 文档收尾 |
| 9 | 技术栈零新增（运行时） | ✅ | playwright 只在 dev extras；lock 的 dev 组；Docker 不装（R-02 实测） |
| 10 | 前端栈不破 | ✅ | 前端零改动 |
| 11 | PG16 + Redis + S3 不变 | ✅ | 无 schema 变更 |
| 12 | LLM 经 LiteLLM | ✅ | 无 LLM 调用（B4 只建 Run 不等待执行） |
| 13 | 自研编排器禁 LangGraph | ✅ | 不碰执行引擎 |
| 14 | 沙箱隔离 | ✅ | 无代码执行 |
| 15 | API RT < 100ms | ✅ | 验收脚本不计 RT；但 SC-003 管脚本自身 <180s |
| 16 | WebSocket 推送 | ✅ | 不涉及 |
| 17 | 部署 Compose + Caddy | ✅ | 不改 deploy.sh；dev extras 不进镜像 |
| 18 | JWT + RBAC 三角色 | ✅ | 验收对象含 viewer 门（A5/C5） |
| 19 | 凭证加密 | ✅ | 无凭证字段 |
| 20 | 脚本只跑沙箱 | ✅ | s12 本身是脚本，只驱动浏览器与 API |

**违例登记（Complexity Tracking）**：无。唯一「看起来更简单」的方案
（Node 版 Playwright，本机已装）被 R-02 以「偏离用户确认的 Python 形态」
否掉；代理安装成功后回归 Python。

## Phase 拆分（每 Phase 一次提交）

| Phase | 内容 | 任务 | 提交点 |
|---|---|---|---|
| P2 基建设施 | playwright dev extras（已完成）+ 脚本骨架（CLI/浏览器解析/种子/登录/产物/dev server 自管理）+ 冒烟一项 | T001–T006 | `test(verify): 011 s12 骨架与基建设施` |
| P3 清单自动化 | A 组 5 项 → B 组 5 项 → C 组 5 项（按组提交） | T007–T011 | `test(verify): 011 A 组（008 清单）` 等三次 |
| P4 回填与回归 | 三份 quickstart 回填 + s 系列全回归 + SC-005 反证实验 | T012–T016 | `docs(spec): 011 三份清单回填` 等 |
| P5 收尾合并 | README 登记 + .gitignore 核对 + ff 合并 | T017–T020 | 每项一次提交 |

并行组：A/B/C 三组相互独立（种子已分开建），可并行开发。

## 风险与回滚

- **dnd-kit 拖拽在无头环境不响应**：R-06 的退化路径（点选 + 注明）；
  若三组全过但 B4 走了退化，回填时如实写「（退化：点选）」。
- **dev server 启动慢/首屏超时**：条件轮询（等 `networkidle` 或
  选择器出现）而非固定 sleep；SC-003 的 180s 预算兜底。
- **浏览器 revision 漂移**：R-01 的缓存扫描 + `S12_CHROMIUM_PATH`
  覆盖；全失败明确退出而非静默跳过。
- **验收脚本自身 flaky**：SC-004 的可重复跑（随机后缀 + 不依赖残留）
  是防线；若某项两次结果不同，记入 quickstart 而不是当成通过。
- **回滚面**：本特性零应用代码改动——回滚 = 删脚本 + revert 文档，
  应用行为零影响（这正是 R-08 边界换来的）。
