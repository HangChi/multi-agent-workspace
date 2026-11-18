/**
 * 顶栏：只承载「我在哪」和「全局开关」，不再堆放模块入口。
 *
 * 左：移动端菜单按钮 + 面包屑（工作台 / 看板、设置 / 定时调度）。
 * 右：系统状态、主题、移动端账号菜单。
 * 桌面端的账号菜单在侧栏底部，这里只在窄屏出现，避免两套菜单互相打架。
 */

import { Link } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Dropdown, DropdownItem, DropdownLabel, DropdownSeparator } from '../ui/Dropdown'
import { Icon } from '../ui/Icons'
import { ThemeToggle } from '../ui/ThemeToggle'
import { cn } from '../ui/cn'
import { useCommandPalette } from '../command/useCommandPalette'
import { resolveTrail } from './nav'

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

interface Props {
  pathname: string
  onOpenMenu: () => void
}

export function Topbar({ pathname, onOpenMenu }: Props) {
  const { user, logout } = useAuth()
  const { toggle } = useCommandPalette()
  const trail = resolveTrail(pathname, user?.role)
  const initial = (user?.email ?? '?').slice(0, 1).toUpperCase()

  return (
    <header className="sticky top-0 z-30 border-b border-line/70 bg-canvas/80 backdrop-blur-xl">
      <div className="flex h-14 items-center gap-2 px-3 sm:px-5 lg:px-7">
        <button
          type="button"
          onClick={onOpenMenu}
          aria-label="打开导航菜单"
          className="flex h-10 w-10 items-center justify-center rounded-lg text-ink-soft transition-colors hover:bg-surface-subtle hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 lg:hidden"
        >
          <Icon name="menu" className="h-[18px] w-[18px]" />
        </button>

        <nav aria-label="面包屑" className="flex min-w-0 items-center gap-1.5 text-ui">
          <span className="hidden text-ink-faint sm:inline">{trail.module}</span>
          {trail.module && trail.page && (
            <Icon
              name="chevron-right"
              className="hidden h-3 w-3 shrink-0 text-ink-faint sm:block"
            />
          )}
          <span className="truncate font-medium text-ink">{trail.page ?? '工作台'}</span>
        </nav>

        {/* 全局搜索：主流工作台把「去哪」收敛成一个 ⌘K 入口，
            比在顶栏堆一排入口更省空间，也更快 */}
        <button
          type="button"
          onClick={toggle}
          aria-label="打开命令面板"
          className="ml-auto hidden h-9 items-center gap-2 rounded-lg border border-line bg-surface pl-2.5 pr-2 text-ui text-ink-faint shadow-card transition-colors hover:border-line-strong hover:text-ink-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 sm:flex"
        >
          <Icon name="search" className="h-3.5 w-3.5" />
          <span className="w-24 text-left">搜索或跳转…</span>
          <kbd className="rounded border border-line bg-surface-muted px-1 py-0.5 text-micro font-medium">
            ⌘K
          </kbd>
        </button>

        <div className="ml-auto flex items-center gap-1.5 sm:ml-0">
          <span className="mr-1 hidden items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-micro font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200/70 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/25 md:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse-ring" />
            系统正常
          </span>

          <ThemeToggle className="h-9 w-9 rounded-lg" />

          <div className="lg:hidden">
            <Dropdown
              ariaLabel="账号菜单"
              panelClassName="w-60"
              triggerClassName="py-1 pl-1 pr-1.5 hover:bg-surface-subtle"
              trigger={
                <>
                  <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-micro font-semibold text-white">
                    {initial}
                  </span>
                  <Icon name="chevron-down" className="h-3.5 w-3.5 text-ink-faint" />
                </>
              }
            >
              <div className="px-2.5 pb-2 pt-1.5">
                <p className="truncate text-ui font-medium text-ink">{user?.email}</p>
                <div className="mt-1.5">
                  <Badge tone={ROLE_TONE[user?.role ?? 'member'] ?? 'slate'} dot>
                    {ROLE_LABEL[user?.role ?? 'member'] ?? user?.role}
                  </Badge>
                </div>
              </div>
              <DropdownSeparator />
              <DropdownLabel>账号</DropdownLabel>
              <Link to="/settings/general">
                <DropdownItem icon="settings">通用设置</DropdownItem>
              </Link>
              <DropdownItem icon="log-out" tone="danger" onClick={logout}>
                退出登录
              </DropdownItem>
            </Dropdown>
          </div>
        </div>
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
