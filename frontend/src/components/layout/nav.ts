/**
 * 导航结构（单一真实来源）。
 *
 * 为什么把配置从 AppLayout 里抽出来：
 * 1. 侧栏、移动端抽屉、顶栏面包屑三处都要读同一份数据，
 *    写三遍必然漂移（此前顶栏就漏掉了成员角色过滤）。
 * 2. 「设置」是一级模块，但它内部的条目仍是页面，
 *    把 MAIN_NAV / SETTINGS_NAV 分成两组后，侧栏只渲染 4+1 项，
 *    顶栏不再挤成一条 8 项的长龙 —— 这正是本次重排要解决的问题。
 */

import type { IconName } from '../ui/Icons'

export interface NavItem {
  to: string
  label: string
  /** 侧栏与面包屑共用的短名（缺省用 label） */
  shortLabel?: string
  icon: IconName
  /** 精确匹配路由（首页用，否则任何路径都会高亮它） */
  end?: boolean
  adminOnly?: boolean
  memberOnly?: boolean
  description?: string
}

/** 主导航：日常真正会反复切换的四个入口。 */
export const MAIN_NAV: NavItem[] = [
  { to: '/', label: '需求看板', shortLabel: '看板', icon: 'grid', end: true },
  { to: '/dashboard', label: '仪表盘', icon: 'activity' },
  { to: '/agents', label: 'Agent 管理', shortLabel: 'Agent', icon: 'bot' },
  { to: '/groups', label: 'Agent 组', shortLabel: '组', icon: 'users' },
]

/**
 * 设置模块。
 *
 * 模板、调度、工具市场、管理后台的共同点：低频、配置性质、需要成组解释。
 * 放在设置里既腾出了主栏，也让「我改了什么配置」有唯一入口。
 */
export const SETTINGS_NAV: NavItem[] = [
  {
    to: '/settings/general',
    label: '通用设置',
    shortLabel: '通用',
    icon: 'settings',
    description: '外观主题、账号信息与系统状态',
  },
  {
    to: '/settings/templates',
    label: 'Agent 模板',
    shortLabel: '模板',
    icon: 'layers',
    memberOnly: true,
    description: '把调好的配置沉成模板，一键复用',
  },
  {
    to: '/settings/schedules',
    label: '定时调度',
    shortLabel: '调度',
    icon: 'clock',
    memberOnly: true,
    description: '按 cron 周期自动建单并分配',
  },
  {
    to: '/settings/marketplace',
    label: '工具市场',
    shortLabel: '工具',
    icon: 'plug',
    description: '浏览并安装已审核的第三方 MCP 工具',
  },
  {
    to: '/settings/admin',
    label: '管理后台',
    shortLabel: '管理',
    icon: 'shield',
    adminOnly: true,
    description: '全站审计日志与外部工具服务',
  },
]

export type Role = 'admin' | 'member' | 'viewer' | undefined

/** 角色过滤：viewer 看不到调度与模板，非 admin 看不到管理后台。 */
export function canSee(item: NavItem, role: Role): boolean {
  if (item.adminOnly && role !== 'admin') return false
  if (item.memberOnly && role !== 'admin' && role !== 'member') return false
  return true
}

export function filterNav(items: NavItem[], role: Role): NavItem[] {
  return items.filter((item) => canSee(item, role))
}

/** 当前路径命中的设置条目（设置页内部二级导航高亮用）。 */
export function activeSettingsItem(pathname: string): NavItem | undefined {
  return SETTINGS_NAV.find((item) => pathname.startsWith(item.to))
}
