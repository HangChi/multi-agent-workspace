/**
 * 顶栏：横向导航（桌面单行 / 移动端第二行可横滑）+ 右侧全局操作。
 *
 * 为什么从侧栏改回横栏：
 * - 侧栏常驻会吃掉 256px 内容宽度，看板列被压窄；而这个工作台的页面
 *   几乎都是「宽内容」（看板、列表、表格），横向导航更划算；
 * - 于是横栏只放 4 个业务入口（看板、仪表盘、Agent、组）。
 *
 * 设置不进横栏，全部收进右上角「个人菜单」（头像下拉）：账号信息 +
 * 通用/模板/调度/工具市场/管理后台 + 退出。这是 Linear / GitHub 的做法——
 * 设置是低频且属于「我自己」的操作，不该和业务入口抢导航位。
 *
 * 「系统正常」只在这里出现一次：页脚、设置页各显示一遍没有意义，
 * 顶栏是唯一合理的实时状态位；版本号收敛到设置 → 通用设置里。
 */

import { Link, NavLink } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Dropdown, DropdownItem, DropdownLabel, DropdownSeparator } from '../ui/Dropdown'
import { Icon } from '../ui/Icons'
import { LogoMark } from '../ui/Logo'
import { ThemeToggle } from '../ui/ThemeToggle'
import { cn } from '../ui/cn'
import { useCommandPalette } from '../command/useCommandPalette'
import { MAIN_NAV, SETTINGS_NAV, filterNav } from './nav'

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
    'inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg px-2.5 text-ui font-medium transition-colors duration-150',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2',
    isActive
      ? 'bg-brand-50 text-accent ring-1 ring-inset ring-brand-100 dark:bg-brand-500/10'
      : 'text-ink-soft hover:bg-surface-subtle hover:text-ink'
  )
}

export function Topbar() {
  const { user, logout } = useAuth()
  const { toggle } = useCommandPalette()
  const mainItems = filterNav(MAIN_NAV, user?.role)
  const settingsItems = filterNav(SETTINGS_NAV, user?.role)
  const initial = (user?.email ?? '?').slice(0, 1).toUpperCase()

  return (
    <header className="sticky top-0 z-40 border-b border-line/70 bg-canvas/85 backdrop-blur-xl">
      <div className="mx-auto flex h-14 w-full max-w-[1560px] items-center gap-2 px-3 sm:gap-3 sm:px-6 lg:px-8">
        <Link
          to="/"
          className="flex shrink-0 items-center gap-2 rounded-lg pr-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2"
        >
          <LogoMark size={26} title="" />
          <span className="hidden text-[14.5px] font-semibold tracking-tight text-ink sm:block">
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
          {/* 全局搜索：主流工作台把「去哪」收敛成一个 ⌘K 入口 */}
          <button
            type="button"
            onClick={toggle}
            aria-label="打开命令面板"
            className="hidden h-9 items-center gap-2 rounded-lg border border-line bg-surface pl-2.5 pr-2 text-ui text-ink-faint shadow-card transition-colors hover:border-line-strong hover:text-ink-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 sm:flex"
          >
            <Icon name="search" className="h-3.5 w-3.5" />
            <span className="w-20 text-left lg:w-24">搜索或跳转…</span>
            <kbd className="rounded border border-line bg-surface-muted px-1 py-0.5 text-micro font-medium">
              ⌘K
            </kbd>
          </button>

          <span className="mr-1 hidden items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-micro font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200/70 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/25 lg:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse-ring" />
            系统正常
          </span>

          <ThemeToggle className="h-9 w-9 rounded-lg" />

          <Dropdown
            ariaLabel="个人菜单"
            panelClassName="w-64"
            triggerClassName="py-1 pl-1 pr-1.5 hover:bg-surface-subtle"
            trigger={
              <>
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-[11px] font-semibold text-white">
                  {initial}
                </span>
                <Icon name="chevron-down" className="hidden h-3.5 w-3.5 text-ink-faint sm:block" />
              </>
            }
          >
            {/* 个人模块：账号信息 + 全部设置入口 + 退出。
                设置不再占顶栏，全部收在这里——这是 Linear / GitHub 的做法 */}
            <div className="px-2.5 pb-2 pt-1.5">
              <p className="truncate text-ui font-medium text-ink">{user?.email}</p>
              <div className="mt-1.5">
                <Badge tone={ROLE_TONE[user?.role ?? 'member'] ?? 'slate'} dot>
                  {ROLE_LABEL[user?.role ?? 'member'] ?? user?.role}
                </Badge>
              </div>
            </div>

            {settingsItems.length > 0 && (
              <>
                <DropdownSeparator />
                <DropdownLabel>设置</DropdownLabel>
                {settingsItems.map((item) => (
                  <Link key={item.to} to={item.to}>
                    <DropdownItem icon={item.icon}>{item.label}</DropdownItem>
                  </Link>
                ))}
              </>
            )}

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
          className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-canvas via-canvas/80 to-transparent"
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
