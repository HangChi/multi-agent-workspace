# Research: 013 s12 发现修复

## R-01：发现②的一半是 s12 自己的时序 bug

- **决策**：`_open_detail` 等 `GET /requests/{id}` 响应完成再断言
  （FR-006），同时把 B1 的边列表断言从 section 定位器改为对话框级。
- **依据**：实测 2026-11-18——BoardPage 的点击开详情是两段式
  （`setDetail(列表项)` 秒开 → `fetchRequest().then(setDetail)` 补全），
  带 2.5s 等待后区块显示「前置需求 1 个」+ 提示「有 1 个前置未完成」
  全部正确。011 的 B1/B2 失败是脚本没等拉取，不是 app bug。
- **被否决**：把 `detailSource` 优先级改成深链优先——app 本来就会补，
  改它反而破坏「秒开」UX（列表数据先渲染）。

## R-02：弹层刷新用回调而非把 detail 改成 React Query

- **决策**：`DependencySection` 加 `onChanged` 回调，BoardPage 重拉
  当前弹层需求（FR-004/005）。
- **依据**：`detail` 是 BoardPage 的局部 state（刻意为之：秒开 UX +
  列表刷新不重置弹层），`invalidateQueries` 对它无效。改成
  `useQuery(['requests','detail',id])` 会丢掉秒开（新 key 无数据，
  弹层要等 fetch）——用 `placeholderData` 也救不回「点开的瞬间就有
  列表数据」。回调是 6 行的最小改动。
- **被否决**：detail 改 query——UX 回归（秒开变等圈）；DependencySection
  自己 `setQueryData` 伪造详情——绕过服务端真相，删边的 terminal 状态
  会算错。

## R-03：scope 切 relevant 而非页面加切换器

- **决策**：ProjectsPage 与 RequestForm 的查询直接切 `relevant`
  （FR-002/003），不加「我的/共享的」tab。
- **依据**：010 的规格原话就是「项目卡片/列表 MUST 区分我的与共享的」
  ——区分靠徽章（已有），不需要两个 tab；且看板下拉一直是 relevant，
  表单与页面跟它一致才是最小惊讶。owner 的写操作门在 MemberSection
  的 `isOwner` 判断（与列表 scope 正交），切 relevant 不破坏权限。
- **被否决**：加 scope 切换 tab——010 规格没要，且多一个状态就要多
  一套 s12 断言。
