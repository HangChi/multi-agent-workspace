/**
 * 顶栏：横向导航（桌面单行 / 移动端第二行可横滑）+ 右侧全局操作。
 *
 * 为什么从侧栏改回横栏：
 * - 侧栏常驻会吃掉 256px 内容宽度，看板列被压窄；而这个工作台的页面
 *   几乎都是「宽内容」（看板、列表、表格），横向导航更划算；
 * - 于是横栏放 5 个业务入口（看板、仪表盘、Agent、组、项目）。
 *
 * 设置不进横栏，收在右上角「个人菜单」（头像下拉）里：账号信息 + 一个
 * 「设置」入口 + 退出。这是 Linear / GitHub 的做法——设置是低频且属于
 * 「我自己」的操作，不该和业务入口抢导航位。
 *
 * 菜单里只放**一个**设置入口，不罗列通用/模板/调度/工具/管理五个子项：
 * 那五个目的地的完整导航在设置页自己的二级导航里（SettingsLayout），
 * 头像菜单再列一遍就是同一份清单两个入口，点进去看到的内容也完全相同。
 * 需要直达某个设置子页时用命令面板（已索引全部设置页）。
 *
 * 右上角保持克制：只有命令面板、明/暗切换、通知、个人菜单。
 * 「系统正常」pill 已撤掉——它既不是真监控（没有后端探活背书），
 * 又和设置里的主题、版本信息重复；主题的三选一在设置 → 通用设置。
 */

import { Link, NavLink, useNavigate } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Dropdown, DropdownItem, DropdownSeparator } from '../ui/Dropdown'
import { Icon } from '../ui/Icons'
import { LogoMark } from '../ui/Logo'
import { ThemeToggle } from '../ui/ThemeToggle'
import { cn } from '../ui/cn'
import { useCommandPalette } from '../command/useCommandPalette'
import { NotificationBell } from './NotificationBell'
import { MAIN_NAV, filterNav } from './nav'
import { modCombo } from '../../utils/platform'

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  member: '成员',
  viewer: '只读',
}

const ROLE_TONE: Record<string, BadgeTone> = {
  admin: 'brand',
  member: 'sky',
  viewer: 'slate',
}

function navClass({ isActive }: { isActive: boolean }) {
  return cn(
    'inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-ui font-medium transition-all duration-150',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2',
    isActive
      ? // 激活态：品牌淡蓝底 + 深蓝字 + 内描边。三件套一起才像「选中的标签」，
        // 只有文字变色在白底上几乎看不出来
        'bg-surface-brand text-accent ring-1 ring-inset ring-brand-200/70 dark:ring-brand-500/25'
      : 'text-ink-soft hover:bg-surface-subtle hover:text-ink'
  )
}

export function Topbar() {
  const { user, logout } = useAuth()
  const { toggle } = useCommandPalette()
  const navigate = useNavigate()
  const mainItems = filterNav(MAIN_NAV, user?.role)
  const initial = (user?.email ?? '?').slice(0, 1).toUpperCase()

  return (
    // 白底顶栏压在灰蓝画布上：明度差给页面「建立了结构」，
    // 此前顶栏与内容同色（canvas/85），页面像一块没有框架的平板
    <header className="sticky top-0 z-header border-b border-line/80 bg-surface/85 backdrop-blur-xl">
      <div className="mx-auto flex h-14 w-full max-w-[1560px] items-center gap-2 px-3 sm:gap-3 sm:px-6 lg:px-8">
        <Link
          to="/"
          className="flex shrink-0 items-center gap-2 rounded-lg pr-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2"
        >
          <LogoMark size={26} title="" />
          <span className="hidden text-body font-semibold tracking-tight text-ink sm:block">
            Agent 工作台
          </span>
        </Link>

        {/* 桌面横向导航：只放业务入口，设置走头像菜单（见下方账号菜单） */}
        <nav className="hidden items-center gap-0.5 md:flex" aria-label="主导航">
          {mainItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={navClass}>
              <Icon name={item.icon} className="h-3.5 w-3.5 opacity-80" />
              {item.shortLabel ?? item.label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-1.5">
          {/* 全局搜索：主流工作台把「去哪」收敛成一个命令面板入口。
              窄屏退化为纯图标按钮——触屏没有硬件键盘，不能整个撤掉入口。 */}
          <button
            type="button"
            onClick={toggle}
            aria-label="打开命令面板"
            className="inline-flex h-9 shrink-0 items-center justify-center rounded-lg border border-line bg-surface px-2 text-ink-faint shadow-card transition-colors hover:border-line-strong hover:text-ink-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 sm:hidden"
          >
            <Icon name="search" className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={toggle}
            aria-label="打开命令面板"
            className="hidden h-9 items-center gap-2 rounded-lg border border-line bg-surface pl-2.5 pr-2 text-ui text-ink-faint shadow-card transition-colors hover:border-line-strong hover:text-ink-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 sm:flex"
          >
            <Icon name="search" className="h-3.5 w-3.5" />
            <span className="w-20 text-left lg:w-24">搜索或跳转…</span>
            <kbd className="rounded border border-line bg-surface-muted px-1 py-0.5 text-2xs font-medium">
              {modCombo('K')}
            </kbd>
          </button>

          {/* 一键明暗切换：三选一（含跟随系统）在设置 → 通用设置里 */}
          <ThemeToggle variant="toggle" />

          {/* 007：通知铃铛（未读数轮询 ≤30s，见 NotificationBell） */}
          <NotificationBell />

          <Dropdown
            ariaLabel="个人菜单"
            panelClassName="w-64"
            triggerClassName="py-1 pl-1 pr-1.5 hover:bg-surface-subtle"
            trigger={
              <>
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-2xs font-semibold text-white">
                  {initial}
                </span>
                <Icon name="chevron-down" className="hidden h-3.5 w-3.5 text-ink-faint sm:block" />
              </>
            }
          >
            {/* 个人模块：账号信息 + 单一设置入口 + 退出。
                五个设置子页不在这里罗列——设置页的二级导航是它们唯一的
                完整清单，两处各列一遍就是双入口重复。 */}
            <div className="px-2.5 pb-2 pt-1.5">
              <p className="truncate text-ui font-medium text-ink">{user?.email}</p>
              <div className="mt-1.5">
                <Badge tone={ROLE_TONE[user?.role ?? 'member'] ?? 'slate'} dot>
                  {ROLE_LABEL[user?.role ?? 'member'] ?? user?.role}
                </Badge>
              </div>
            </div>

            <DropdownSeparator />
            {/* 用 navigate 而不是 <Link> 包 button：嵌套交互元素既不符合 HTML
                规范，也会让焦点在菜单卸载后坠回 body；Dropdown 的 panel
                onClick 会顺手把焦点还给头像按钮 */}
            <DropdownItem icon="settings" onClick={() => navigate('/settings/general')}>
              设置
            </DropdownItem>

            <DropdownSeparator />
            <DropdownItem icon="log-out" tone="danger" onClick={logout}>
              退出登录
            </DropdownItem>
          </Dropdown>
        </div>
      </div>

      {/* 移动端：第二行可横向滚动的导航（右侧渐隐提示还能滑） */}
      <div className="relative border-t border-line-subtle md:hidden">
        <nav
          className="scrollbar-none flex items-center gap-1 overflow-x-auto px-3 py-1.5"
          aria-label="主导航（移动端）"
        >
          {mainItems.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={navClass}>
              <Icon name={item.icon} className="h-3.5 w-3.5 opacity-80" />
              {item.shortLabel ?? item.label}
            </NavLink>
          ))}
        </nav>
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-surface via-surface/80 to-transparent"
        />
      </div>
    </header>
  )
}

/** 供布局复用的内容区壳：统一内边距与最大宽度。 */
export function ContentShell({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn('mx-auto w-full max-w-[1560px] px-4 py-6 sm:px-6 sm:py-7 lg:px-8', className)}
    >
      {children}
    </div>
  )
}
