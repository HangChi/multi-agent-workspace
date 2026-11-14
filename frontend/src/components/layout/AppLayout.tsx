/** 工作台布局：吸顶毛玻璃导航 + 内容区（T037/T038 共用）。 */

import { Link, NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Dropdown, DropdownItem, DropdownLabel, DropdownSeparator } from '../ui/Dropdown'
import { Icon } from '../ui/Icons'
import type { IconName } from '../ui/Icons'
import { LogoWordmark } from '../ui/Logo'
import { cn } from '../ui/cn'
import { ThemeToggle } from '../ui/ThemeToggle'

interface NavItem {
  to: string
  label: string
  icon: IconName
  end?: boolean
  adminOnly?: boolean
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: '看板', icon: 'grid', end: true },
  { to: '/dashboard', label: '仪表盘', icon: 'activity' },
  { to: '/agents', label: 'Agent', icon: 'bot' },
  { to: '/groups', label: 'Agent 组', icon: 'users' },
  { to: '/admin', label: '管理', icon: 'shield', adminOnly: true },
]

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
    'inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors duration-150',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2',
    isActive
      ? 'bg-brand-50 text-accent ring-1 ring-inset ring-brand-100'
      : 'text-ink-soft hover:bg-surface-subtle hover:text-ink',
  )
}

export function AppLayout() {
  const { user, logout } = useAuth()
  const items = NAV_ITEMS.filter((item) => !item.adminOnly || user?.role === 'admin')
  const initial = (user?.email ?? '?').slice(0, 1).toUpperCase()

  return (
    <div className="relative min-h-screen bg-canvas">
      {/* 顶部极淡的品牌色渐隐，避免纯灰底显得廉价 */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-56 bg-gradient-to-b from-brand-50/60 via-brand-50/10 to-transparent"
      />

      <header className="sticky top-0 z-40 border-b border-line/80 bg-surface/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 w-full max-w-[1600px] items-center gap-4 px-4 sm:px-6 lg:px-8">
          <Link
            to="/"
            className="flex shrink-0 items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2"
          >
            <LogoWordmark size={30} textClassName="hidden sm:flex" />
          </Link>

          <nav className="hidden items-center gap-0.5 md:flex" aria-label="主导航">
            {items.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className={navClass}>
                <Icon name={item.icon} className="h-3.5 w-3.5 opacity-80" />
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-1.5">
            <ThemeToggle />
            <span className="mr-1 hidden items-center gap-1.5 rounded-full bg-emerald-50 px-2 py-1 text-2xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200/70 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/25 lg:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse-ring" />
              系统正常
            </span>

            <Dropdown
              ariaLabel="账号菜单"
              panelClassName="w-64"
              triggerClassName="py-1 pl-1 pr-2 hover:bg-surface-subtle"
              trigger={
                <>
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-brand-600 to-brand-800 text-[11px] font-semibold text-white">
                    {initial}
                  </span>
                  <span className="hidden max-w-[150px] truncate text-[13px] text-ink-soft sm:block">
                    {user?.email}
                  </span>
                  <Icon name="chevron-down" className="hidden h-3.5 w-3.5 text-ink-faint sm:block" />
                </>
              }
            >
              <div className="px-2.5 pb-2 pt-1.5">
                <p className="truncate text-[13px] font-medium text-ink">{user?.email}</p>
                <div className="mt-1.5">
                  <Badge tone={ROLE_TONE[user?.role ?? 'member'] ?? 'slate'} dot>
                    {ROLE_LABEL[user?.role ?? 'member'] ?? user?.role}
                  </Badge>
                </div>
              </div>
              <DropdownSeparator />
              <DropdownLabel>账号</DropdownLabel>
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
            {items.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className={navClass}>
                <Icon name={item.icon} className="h-3.5 w-3.5 opacity-80" />
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-surface via-surface/80 to-transparent"
          />
        </div>
      </header>

      <main className="relative mx-auto w-full max-w-[1600px] px-4 pb-16 pt-5 sm:px-6 sm:pt-6 lg:px-8 lg:pt-8">
        <Outlet />
      </main>
    </div>
  )
}
