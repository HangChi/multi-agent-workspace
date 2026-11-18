# Tasks: 011-browser-walkthrough

> Phase 拆分见 `plan.md`。每 Phase 一次提交。本特性零应用代码改动（R-08）。
> 批量改勾选必须按任务号精确匹配；未实测的项不勾。

## Phase 2 — 基建设施（提交点：`test(verify): 011 s12 骨架与基建设施`）

- [X] T000 [P] pyproject.toml dev extras 加 `playwright>=1.40` + `uv lock` 同步（代理安装）；验证 Python binding 启动本机缓存 headless shell
- [ ] T001 [P] Write `deploy/verify/s12_browser_walkthrough.py` 骨架：CLI（--base/--invite/--viewer-invite/--frontend-port/--frontend-url）+ Result/汇总/退出码（对齐 s1–s11 输出格式）
- [ ] T002 [P] 浏览器解析：`S12_CHROMIUM_PATH` → `PLAYWRIGHT_BROWSERS_PATH` 扫描取最高版本 headless shell → Playwright 默认；全失败明确退出并提示
- [ ] T003 [P] 种子数据（httpx，随机后缀）：注册 owner/mate/stranger/viewer、建 agent、建项目×2、建需求×N、连依赖边、加成员
- [ ] T004 [P] dev server 自管理：子进程拉起 `npm run dev -- --port <port> --strictPort` + 轮询就绪 + finally 回收；`--frontend-url` 分支
- [ ] T005 [P] 失败产物：截图 PNG + innerText 摘要落 `deploy/verify/.artifacts/s12-<ts>/`；`.gitignore` 加该目录
- [ ] T006 [P] 冒烟一项（A4 卡片项目徽章）跑通，证明基建可用

## Phase 3 — 清单自动化（按组三次提交）

- [ ] T007 [P] A 组 5 项（A1 管理页 CRUD / A2 建需求选择器 / A3 项目下拉+URL 深链+刷新保持 / A4 卡片徽章 / A5 viewer 门）跑通
- [ ] T008 [P] B 组 5 项（B1 依赖区块增删 / B2 blocked 提示 / B3 卡片前置徽章+hover / B4 拖拽错误横幅（退化路径注明） / B5 铃铛跳转）跑通
- [ ] T009 [P] C 组 5 项（C1 成员区管理 / C2 成员只读 / C3 成员看板与表单 / C4 铃铛成员通知 / C5 viewer 门）跑通
- [ ] T010 二跑（SC-004 可重复：第二次同样全过，随机后缀不依赖残留）
- [ ] T011 墙钟计时（SC-003 <180s；超了把固定等待改条件轮询）

## Phase 4 — 回填与回归（按文档三次提交 + 回归一次）

- [ ] T012 回填 008 quickstart §2 五条（✅ + 日期 + s12 输出摘要； viewer 未提供则保持 ⏳ 注明）
- [ ] T013 回填 009 quickstart §3 五条（同上纪律；B4 若走退化如实注明）
- [ ] T014 回填 010 quickstart §3 五条（同上纪律）
- [ ] T015 [P] 反证实验（SC-005）：临时破坏一项 → ❌ + 产物 + 退出码非零 → 还原 → 记录到 quickstart §3
- [ ] T016 s11 回归跑通（证明 011 没碰坏应用）

## Phase 5 — 收尾合并（每项一次提交）

- [ ] T017 README 验收脚本表登记 s12 + 011 特性行 + 套件数字说明（前端新增 0 例——验收不进 vitest）
- [ ] T018 docs/architecture.md §12 特性状态 + 011 的语义要点（浏览器验收是「发现与记录」，修另开特性）
- [ ] T019 docs/README 特性表加 011
- [ ] T020 ff 合并 master 删分支 + 更新 MEMORY

## Notes（贯穿全清单的硬约束）

1. **零应用代码改动**（R-08）：走查发现的 bug 记入 quickstart 对应条目
   （保持 ⏳ + 失败原因），另开特性修。本任务的「完成」不含修 bug。
2. **回填绑定跑过**（FR-008）：只有 s12 里 ✅ 的项才允许在 quickstart
   打勾；跳过的项（无 viewer 邀请码）保持 ⏳ 并写「未提供 viewer 邀请码」。
3. **拖拽退化必须注明**（FR-007/R-06）：B4 若走点选路径，输出行与回填
   都写「（退化：点选）」——不假装拖拽通过。
4. **CLI/输出/退出码与 s1–s11 逐字同构**：新脚本不许自创格式
   （汇总行、✅/❌、耗时括号、退出码 0/1）。
5. **种子全随机后缀**（w-<rand>）：断言不依赖空库、不依赖上一次残留
   （SC-004 的防线）。
6. **dev server 端口默认 5199 且冲突即报错**：不静默换端口（R-04）。
7. 代理：如需重装 playwright，`HTTPS_PROXY=http://127.0.0.1:7897`。
8. 迁移：无（零 schema 变更）。
