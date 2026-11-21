/**
 * TanStack Query 的 key 单一来源。
 *
 * 为什么需要：这些字典型 key（活跃 Agent、全部组、员工选项、项目选项）在多处
 * 复用，靠手写字符串前缀天生会漂。已经漂过一次——`SchedulesPage` 用
 * `['agents','list','active','all']`，另外三处用 `['agents','active','all']`，
 * 于是调度页和看板各存一份、互不失效（见 docs/code-audit-2026-11-21.md P6）。
 *
 * 约定：
 * - 前缀 key（如 `requests`）用于 `invalidateQueries` 做批量失效；
 * - 具体 key 用于 `useQuery`。改这两个必须一起改，别只改一边。
 */

/** 前缀 key：只用于失效（invalidateQueries），不要拿去 useQuery。 */
export const PREFIX = {
  requests: ['requests'] as const,
  notifications: ['notifications'] as const,
  agents: ['agents'] as const,
  projects: ['projects'] as const,
  schedules: ['schedules'] as const,
  agentsTemplates: ['agent-templates'] as const,
  projectMembers: ['projectMembers'] as const,
  dependencies: ['dependencies'] as const,
}

/**
 * 字典 key：几乎每个页面都要的「下拉选项」数据。
 *
 * 这类查询跨页复用率最高，也是最该给 staleTime 的——数据本身几分钟内不会变。
 */
export const dictKeys = {
  /** 活跃 Agent 全量（拖拽目标 / 成员候选 / 调度目标）。必须 all: true。 */
  activeAgents: ['agents', 'active', 'all'] as const,
  /** 全部组（同上，分页截断会让第 N 页之后的目标静默消失）。 */
  allGroups: ['groups', 'all'] as const,
  /** 员工选项（007 第三类落点 / 010 项目成员邀请）。 */
  userOptions: ['users', 'options'] as const,
  /** 我的 + 派给我需求所属的项目（008 FR-010）。 */
  projectOptions: ['projects', 'options', 'relevant'] as const,
  /** 工具目录（Agent 配置区 / 模板配置区共用）。 */
  toolsCatalog: ['tools', 'catalog'] as const,
}
