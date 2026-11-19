# Quickstart: 015-ui-refresh 端到端验收记录

**Feature**: `015-ui-refresh` | **Date**: 2026-11-19 | **Branch**: `feat/015-ui-refresh`（已 ff 合并）

> 本文所有 ✅ 均为实测结果。§1–§3 是整改当天（白天）的记录，
> §4–§6 是傍晚补记时重新实测的结果。

## 1. 整改规模

| 检查 | 结果 |
|---|---|
| 提交 | 17 次（M1–M14 + 015b/c/d 三轮定点迭代） |
| 改动 | 69 文件，+3814 / −1757 行 |
| 后端 | 零改动（无接口 / 模型 / 迁移） |
| 新依赖 | 无 |

## 2. 提交前门禁（逐模块）

```bash
cd frontend
npx tsc --noEmit        # 类型
npm run lint            # eslint
npx vitest run          # 单测：143 例 / 26 文件
npm run format:check    # prettier  ← 015 期间漏跑，CI 因此变红（见 §6）
npm run build           # 生产构建
```

| 门禁 | 整改前 | 整改后（2026-11-19 补测） |
|---|---|---|
| 单测 | 137 例 | ✅ 143 例 / 26 文件全通过 |
| 类型 | ✅ | ✅ `tsc --noEmit` 无错 |
| lint | ✅ | ✅ eslint 无告警 |
| 格式 | 未纳入门禁 | ✅ `prettier --check` 全通过（补跑 `--write` 修 24 文件） |
| 构建 | ✅ | ✅ `vite build` 通过 |

## 3. 真实浏览器冒烟（整改当天）

- 环境：`vite dev`（绑 `::1`，需 `--host 127.0.0.1`）+ 8000→8080 代理垫片。
- 账号：member `w-owner-6a204a@test.com`（015 遗留验收账号）。
- 逐页检查：看板 / 项目 / 仪表盘 / Agent / 组 / 运行详情 / 设置各子页——
  无白屏、无控制台报错；拖拽、弹层、命令面板、头像菜单可用。
- 冒烟抓到的回归当场修：命令面板焦点竞争（`panel.focus` vs rAF）、
  键盘拖拽英文播报、`hasFinePointer` 在 jsdom 无 `matchMedia` 时炸测试。

## 4. 008/009/010 走查清单回归（s12，2026-11-19 傍晚补跑）

```bash
backend/.venv/bin/python deploy/verify/s12_browser_walkthrough.py \
  --base http://localhost:8080 \
  --invite VERIFY-011-MEMBER \
  --viewer-invite TEST-VIEWER-CODE-456
```

**首跑 13/15**，两项失败：

| 项 | 现象 | 判定 |
|---|---|---|
| B1 依赖区块 | 等弹层 detach 超时（16.9s），产物截图里**叠着「移除前置依赖？」确认框** | 脚本没跟上 015 新增的移除二次确认 |
| B2 blocked 提示 | 期待「有 1 个前置未完成」，实际「有 2 个」 | 上一条的连带：边没删掉，多留一条 |

修脚本（点完 X 后在标题为「移除前置依赖？」的弹层里点「移除」）后复跑：
**15/15 全绿**（A 5 + B 5 + C 5，种子 `run_id=f0823f`）。

> 结论：015 的 UI 整改**没有破坏** 008/009/010 的任何验收项；掉的两项是
> 验证资产的漂移。这条写进 research R-08 的代价记录。

## 5. 部署（整改当天）

- 单容器替换：`docker tag deploy-frontend:latest aw-frontend:latest` →
  stop / rm `aw-frontend` → `docker run`（网络 `aw_default`，`8080:80`）。
- 线上 bundle：`index-CNtHUzhA.js`（015b 视觉提质后）。
- **不要**在 `deploy/` 下跑 `docker compose up -d frontend`——那会按项目名
  `deploy` 重建整套栈并撞容器名（详见当日工作记录）。

## 6. 已知缺口（诚实记录）

1. **规格先于实现的偏离**：14 次模块提交未先写 spec，补记于当日傍晚。
   见 research R-01 的代价清单。
2. **CI 因本特性变红 5 次**：prettier 漂移（24 文件）被修；同一批红灯里
   另有 0011 迁移枚举与 007 指标治理两处历史债，与本特性无关但同批修掉
   （提交 2d7dcb6 / 9d02e4e / 0fd48e0 / e917fa6）。
3. **深色主题只做冒烟**，无逐页对比度审计；无视觉回归基线。
4. **s12 会写入测试账号**：跑完建议用
   `deploy/verify/cleanup_test_data.py`（dry-run 默认）清理。
