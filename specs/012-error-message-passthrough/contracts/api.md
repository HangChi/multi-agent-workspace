# API Contract: 012 错误消息直通

本特性不改任何 HTTP 契约——它修的是**前端如何显示后端已有的错误消息**。
契约内容是组件入参的判定规则。

## 1. `ErrorBanner` 入参契约（修订后）

```tsx
interface ErrorBannerProps {
  /** 错误来源：原始错误对象（组件提取）或已提取的消息字符串（直通） */
  error: unknown
  variant?: 'banner' | 'inline'
  onDismiss?: () => void
  retry?: () => void
  className?: string
}
```

判定规则：

```
error 为 falsy（null/undefined/''）        → 不渲染
error 为 string                            → 原样渲染（012 新增）
其他                                       → extractError(error)（既有）
```

## 2. 后端错误响应（不变，供对照）

```
409 {"error": {"code": "CONFLICT", "message": "有 1 个前置需求未完成：X（created）"}}
400 {"error": {"code": "INVALID_INPUT", "message": "用户已停用，不能添加为项目成员"}}
```

修复后这些 `message` 全部直达用户（修复前被兜底文案替换）。

## 3. 行为用例表（s12 可验的）

| # | 场景 | 操作 | 修复前 | 修复后 |
|---|---|---|---|---|
| E-1 | 009 门禁 | 拖被挡住需求给 Agent → 确认分配 | 「请求失败，请稍后重试」 | 「有 N 个前置需求未完成：…」 |
| E-2 | 008 同名 | 建同名项目 | 兜底 | 「项目名已存在：X」 |
| E-3 | 010 停用 | 给停用用户加成员 | 兜底 | 「用户已停用，不能添加为项目成员」 |
| E-4 | 字面量 | DashboardPage 统计加载失败 | 兜底 | 「统计加载失败」 |

## 4. 测试契约（ErrorBanner.test.tsx）

| # | 入参 | 断言 |
|---|---|---|
| 1（既有） | axios 形状对象 | 渲染提取 message |
| 2（既有） | null | 不渲染 |
| 3（既有） | axios 形状对象 + inline | P 标签 + message |
| 4（既有） | axios 形状对象 + onDismiss | 关闭回调触发 |
| 5（既有） | axios 形状对象 + retry | 重试按钮 + 回调 |
| 6（新增） | `'具体错误消息'` | 原样渲染 |
| 7（新增） | `''` | 不渲染 |
