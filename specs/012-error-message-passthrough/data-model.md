# Data Model: 012 错误消息直通

零 schema 变更（纯前端组件修复）。数据模型只有入参形态的判定表。

## 1. `ErrorBanner` 入参判定（唯一改动）

| 入参 | 判定 | 渲染 |
|---|---|---|
| `null` / `undefined` / `''` | falsy 短路 | 不渲染（既有行为） |
| `string`（非空） | **新增：直通** | 原样渲染该字符串 |
| axios 形状错误对象 `{response: {data: {error: {message}}}}` | `extractError` | 渲染提取出的 message（既有行为） |
| 其他（Error / 数字 / 未知） | `extractError` | 兜底「请求失败，请稍后重试」（既有行为） |

实现即一行：

```tsx
const message = typeof error === 'string' ? error : extractError(error)
```

## 2. 受影响调用方全景（实测 2026-11-18，共 19 处，**一律不改**）

| 形态 | 处数 | 代表 |
|---|---|---|
| state 存 `string \| null`，`setError(extractError(err))` | 18 | BoardPage（assign/create）、AssignModal、DependencySection、MemberSection、LoginPage、RegisterPage、GroupPage、ProjectsPage、SchedulesPage、AgentPage、AgentTemplatesPage、AdminPage、ExternalToolServers、RejectModal（经父级 state） |
| 字面量字符串 | 2（MarketplacePage、DashboardPage） | `error="工具市场加载失败，请稍后重试。"`、`error={error ?? '统计加载失败'}` |

两类都过同一个组件——组件判型后全部受益。

## 3. 明确不做

- ❌ 改 `extractError`（client.test.ts 的四条用例是它的契约，不动）。
- ❌ 改任何调用方（R-01：回归面 19 处 vs 组件一处）。
- ❌ 改任何后端代码。
- ❌ 新增错误码/错误类型体系（超出本特性）。
