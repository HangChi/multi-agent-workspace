# API Contract: 014 测试数据卫生

零 HTTP 契约变更。两个交付物的接口面：

## 1. 清理工具 CLI

```
backend/.venv/bin/python deploy/verify/cleanup_test_data.py [--execute]
    [--pattern 'w-%' ...] [--protect 'owner@example.com' ...]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--execute` | 关 | 真删；默认 dry-run 只打印 |
| `--pattern` | 七种测试前缀 | 邮箱 LIKE 白名单（可多次） |
| `--protect` | owner@example.com | 永不删除的账号（可多次） |

DSN 硬编码本地测试库（`127.0.0.1:5432/agent_workbench`）——这是本地
运维工具，不连生产。

## 2. AssignTargets 组件契约（重写后）

```tsx
<AssignTargets agents={Agent[]} groups={Group[]} users={UserOption[]} />
```

- 渲染三行：`同事 (N)`（带 `aria-label="筛选分配同事"` 过滤框）、
  `Agent (N)`、`组 (N)`；每行超 8 个折叠出「+N 展开」。
- **拖拽契约不变**：每个芯片是 `useDroppable({ id: 'user|agent|group:<id>' })`，
  `title` = `拖到这里分派给/分配给「name」 · meta`（s12 B4 依赖）。
- 三类全空 → 引导文案（旧版行为）。
