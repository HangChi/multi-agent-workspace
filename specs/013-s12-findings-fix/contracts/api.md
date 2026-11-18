# API Contract: 013 s12 发现修复

零 HTTP 契约变更（纯前端 + 验收脚本）。既有端点消费方式的变化：

## 1. 前端查询的 scope 变化（消费同一端点）

| 调用方 | 修复前 | 修复后 |
|---|---|---|
| `GET /projects`（ProjectsPage 列表） | `?scope=mine` | `?scope=relevant` |
| `GET /projects`（RequestForm 选择器） | `?scope=mine` | `?scope=relevant` |

后端 010 已支持 relevant（我创建的 ∪ 派给我需求所属的 ∪ 我是成员的），
本特性只把前端接上。

## 2. 弹层刷新时序（新内部契约）

```
用户点「添加」→ addDependency API → onSuccess:
  invalidateQueries(['requests'])   # 看板列表/卡片徽章刷新
  onChanged?.()                     # 父级重拉详情快照（013 新增）
    → BoardPage.refreshDetail(id)
      → GET /requests/{id} → setDetail → 弹层区块立即显示新边
```

## 3. s12 的 `_open_detail` 时序契约（FR-006）

```
点卡片「详情」→ 等 dialog 出现 → 等 GET /api/v1/requests/{id} 响应
（应用的两段式：秒开列表数据 → 补全详情）→ 才允许断言依赖区块
```
