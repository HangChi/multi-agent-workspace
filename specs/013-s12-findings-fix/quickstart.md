# Quickstart: 013-s12-findings-fix 端到端验收记录

**Feature**: `013-s12-findings-fix` | **Date**: 2026-11-18`

> 本文所有 ✅ 均为真实栈 + 真实浏览器实测。

## 1. 验收命令

```bash
backend/.venv/bin/python deploy/verify/s12_browser_walkthrough.py \
  --base http://localhost:8080 --invite <INVITE> --viewer-invite <VIEWER-INVITE>
```

## 2. 实测记录

| 日期 | 命令 | 结果 |
|---|---|---|
| 2026-11-18 | s12 复跑（15 项） | ✅ **15/15 全绿**（修复前 9/15） |
| 2026-11-18 | s12 二跑（稳定性） | ✅ 15/15 一致 |
| 2026-11-18 | 前端五件套 | ✅ lint/format/typecheck/139 passed/build |
| 2026-11-18 | 后端全量 | ✅ 1462 passed（零后端改动验证） |
| 2026-11-18 | 三份 quickstart 回填 | ✅ 008 A5 / 009 B1+B2 / 010 C2+C3+C5 全转 ✅ |

## 3. 修复对照

| s12 项 | 修复前 | 根因 | 修复 |
|---|---|---|---|
| A5/C5 | ❌ viewer 看得见项目入口 | nav 无 memberOnly | `nav.ts` 加 `memberOnly: true` |
| C2 | ❌ 成员项目页无共享项目 | ProjectsPage scope=mine | 切 `relevant` |
| C3 | ❌ 建需求表单无共享项目 | RequestForm scope=mine | 切 `relevant` |
| B1 | ❌ 加边后移除按钮不出现 | 弹层快照不吃 invalidate + s12 没等拉取 | `onChanged` 回调 + `_open_detail` 等待 |
| B2 | ❌ 提示显示「有 0 个」 | 同 B1（级联） | 同 B1 |
