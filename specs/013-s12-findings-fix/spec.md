# Feature Specification: s12 发现修复（013）

**Feature Branch**: `feat/013-s12-findings-fix`

**Created**: 2026-11-18

**Status**: Draft

**Input**: 用户 2026-11-18「继续修，全部修完」——把 011 的 s12 首轮抓出、
012 修完 ErrorBanner 后仍挂账的 6 项（A5/B1/B2/C2/C3/C5）全部修到
s12 全绿。三项根因（011 quickstart §5 已定位）。

## User Scenarios & Testing *(mandatory)*

### US1 — 成员在自己的项目页看见共享项目（优先级 P1）

作为项目成员，我打开项目管理页应该看到**共享给我**的项目（带「共享的」
徽章），建需求时也能把需求归到共享项目里。

**Acceptance Scenarios**：
1. **Given** 我是某项目的成员（非创建者），**When** 打开 `/projects`，
   **Then** 列表含该项目且徽章为「共享的」。
2. **Given** 同上，**When** 在看板开「新建需求」表单，**Then** 项目选择器
   含该共享项目。

### US2 — 依赖区块在弹层里完整可用（优先级 P1）

作为用户，我在详情弹层里能看见已有前置边、连新边、删边——操作后弹层
**立即**反映变化。

**Acceptance Scenarios**：
1. **Given** 需求有 1 条未完成前置，**When** 点卡片开详情，**Then**
   区块显示「前置需求 1 个」+ 该边（标题+状态）+ 顶部提示
   「有 1 个前置未完成，暂不能分配执行」。
2. **Given** 弹层已开，**When** 用添加器连一条新边，**Then** 区块立即
   出现该边与其移除按钮。
3. **Given** 弹层已开，**When** 点某边的移除按钮，**Then** 该边立即从
   区块消失。

### US3 — viewer 看不到项目入口（优先级 P2）

作为 viewer，我的导航里不应该出现「项目」——点进去只会吃 403。

**Acceptance Scenarios**：
1. **Given** viewer 登录，**When** 看导航，**Then** 无「项目」项。
2. **Given** viewer 直接访问 `/projects`，**Then** 后端 403（既有契约），
   页面渲染错误态而非空白。

### Edge Cases

- **项目页切到 relevant 后**：owner 的操作（改名/归档/删除/成员管理）
  对共享项目必须仍然只对创建者可见（MemberSection 已有 owner 门，
  不能被 relevant 破坏）。
- **成员在共享项目里建需求**：`project_id` 归属校验走
  `load_active_owned_project`（010 已扩展为「owner 或成员」），无需再改。
- **弹层重拉失败**：`refreshDetail` 失败时保持原快照（既有降级行为），
  不弹错。

## Requirements *(mandatory)*

- **FR-001**：`nav.ts` 的 `/projects` 项 MUST 加 `memberOnly: true`。
- **FR-002**：`ProjectsPage` 的项目列表查询 MUST 从 `scope: 'mine'`
  改为 `scope: 'relevant'`（010 后端已支持）；queryKey 同步改。
- **FR-003**：`RequestForm` 的项目选择器查询 MUST 从 `scope: 'mine'`
  改为 `scope: 'relevant'`。
- **FR-004**：`DependencySection` MUST 提供 `onChanged` 回调，增删边
  成功后调用（父级重拉详情快照）。
- **FR-005**：`BoardPage` MUST 把 `onChanged` 接到「重拉当前弹层需求
  详情」上。
- **FR-006**：s12 的 `_open_detail` MUST 等详情拉取完成再断言
  （011 曾把该时序误判成 app bug——脚本自身的修正是本特性的一部分）。
- **FR-007**：修完 MUST 经 s12 复跑验证：15/15 全绿（A5/B1/B2/C2/C3/C5
  六项转 ✅）。
- **FR-008**：本特性 MUST NOT 改后端；MUST NOT 改三份清单的验收对象
  （s12 断言不动，只修 `_open_detail` 的等待与 B1 的定位器）。

### Key Entities

- 无新实体。改动集中在 4 个前端文件 + 1 个验收脚本。

## Success Criteria *(mandatory)*

- **SC-001**：s12 复跑 **15/15**（当前 9/15）。
- **SC-002**：前端五件套全绿；既有 139 例零回归。
- **SC-003**：三份 quickstart 的对应 ⏳ 条目回填 ✅（008 A5、009 B1/B2、
  010 C2/C3/C5），011 §5 的三项发现标记已修复。

## Assumptions

- 三项根因 011 已定位（quickstart §5），本特性不重新论证，直接修。
- 「发现②」实际由两部分组成：app 缺刷新回调（FR-004/005）+ s12 没等
  拉取（FR-006）——前者是真 bug，后者是脚本时序；都修。
- 项目页切 relevant 不影响 owner 权限门（MemberSection 的 owner 判断
  与列表 scope 正交）。
