# Quickstart: 012-error-message-passthrough 端到端验收记录

**Feature**: `012-error-message-passthrough` | **Date**: 2026-11-18 | **Branch**: `feat/012-error-message-passthrough`

> 本文所有 ✅ 均为**真实栈 + 真实浏览器**上的实测结果。

## 0. 背景

011 的 s12 首轮跑出 8/15，其中 B4（拖被挡住需求分配）失败的真实根因
不是拖拽、不是 409——是 `ErrorBanner` 对**已提取的字符串**二次
`extractError`，所有后端错误消息都被替换成「请求失败，请稍后重试」。
本特性修它。

## 1. 单元验收（前端 vitest）

```bash
cd frontend && npx vitest run src/components/ui/ErrorBanner.test.tsx
```

| # | 入参 | 期望 |
|---|---|---|
| 1-5（既有） | axios 形状错误对象 | 提取 message / 不渲染 / inline / onDismiss / retry |
| 6（新增） | `'有 1 个前置需求未完成：X（created）'` | 原样渲染 |
| 7（新增） | `''` | 不渲染 |

## 2. 真实栈验收（s12 复跑）

```bash
backend/.venv/bin/python deploy/verify/s12_browser_walkthrough.py \
  --base http://localhost:8080 --invite <INVITE> --viewer-invite <VIEWER-INVITE>
```

| 项 | 修复前（011 首轮） | 修复后期望 |
|---|---|---|
| B4 拖拽错误横幅 | ❌（只显示通用兜底文案） | ✅（横幅逐条列出阻塞项） |
| 其余 14 项 | 8 过 / 6 败（四项发现） | 零新增失败（B4 单项转绿） |

## 3. 门禁（合并前全量）

```bash
cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build
```

基线（012 前）：前端 137 例全绿；后端 1462 passed（本特性不动后端）。

## 4. 实测记录（待 P3/P4 填写）

| 日期 | 命令 | 结果 |
|---|---|---|
| 2026-11-18 | ErrorBanner.test.tsx（7 例） | ✅ 7/7（新增「字符串原样渲染」先红后绿；「空字符串不渲染」一次通过——falsy 短路本来就在） |
| 2026-11-18 | 前端五件套 | ✅ lint/format/typecheck/139 passed/build 全绿（基线 137+2） |
| 2026-11-18 | s12 复跑（B4） | ✅ ❌ → ✅（错误横幅逐条列出阻塞项；二跑稳定 5.9s/5.8s） |
| 2026-11-18 | s12 三组回归 | ✅ 9 过 / 6 败——6 败是 011 记录的其余已知发现（A5/B1/B2/C2/C3/C5），零新增失败 |
| 2026-11-18 | 009/011 quickstart 回填 | ✅ 009 B4 转 ✅；011 §4/§5 的发现 #2 标记已修复 |

## 5. 部署注记（沙箱网络）

Docker 构建期 `npm ci`/`uv sync` 需要联网，沙箱 DNS 不通。解法：
`docker build --build-arg HTTP_PROXY=http://host.docker.internal:7897
--build-arg HTTPS_PROXY=http://host.docker.internal:7897`（宿主代理
经 host.docker.internal 可达；127.0.0.1 在构建容器里是容器自身），
构建完用 `SKIP_BUILD=1 bash deploy/deploy.sh` 走本地镜像部署。
