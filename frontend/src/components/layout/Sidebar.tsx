/**
 * 侧边栏：桌面固定（可收成图标栏），移动端抽屉。
 *
 * 布局取舍：
 * - 主导航只放 4 个高频入口，其余低频配置全部收进「设置」——
 *   这是本次改版的核心动作：顶栏 8 项挤不下，说明信息架构该分层了。
 * - 账号菜单放在栏底并向上弹出：手在页面底部时不用把鼠标甩回顶部。
 * - 收起态保留图标 + tooltip（title），宽度 76px，仍满足 44px 触摸目标。
 */

import { Link, NavLink, useLocation } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Dropdown, DropdownItem, DropdownLabel, DropdownSeparator } from '../ui/Dropdown'
import { Icon } from '../ui/Icons'
import { LogoMark } from '../ui/Logo'
import { cn } from '../ui/cn'
import { MAIN_NAV, SETTINGS_NAV, filterNav } from './nav'
import type { NavItem } from './nav'

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
  /** 移动端抽屉是否打开 */
  open: boolean
  onClose: () => void
  /** 桌面图标栏收起态（由布局持有，内容区的左偏移要跟着变） */
  collapsed: boolean
  onToggleCollapsed: () => void
}

function navItemClass(collapsed: boolean) {
  return ({ isActive }: { isActive: boolean }) =>
    cn(
      'group relative flex items-center rounded-lg text-ui font-medium transition-all duration-150',
      collapsed ? 'h-11 w-11 justify-center' : 'h-10 gap-2.5 px-2.5',
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2',
      isActive
        ? 'bg-brand-50 text-accent shadow-nav-active ring-1 ring-inset ring-brand-100/90'
        : 'text-ink-soft hover:bg-surface-subtle hover:text-ink'
    )
}

function NavEntry({
  item,
  collapsed,
  onNavigate,
}: {
  item: NavItem
  collapsed: boolean
  onNavigate?: () => void
}) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      title={collapsed ? item.label : undefined}
      className={navItemClass(collapsed)}
    >
      {({ isActive }) => (
        <>
          {/* 激活指示条：图标栏收起时也能一眼定位当前页 */}
          <span
            aria-hidden="true"
            className={cn(
              'absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-brand-500 transition-opacity',
              isActive ? 'opacity-100' : 'opacity-0',
              collapsed && '-left-1.5'
            )}
          />
          <Icon
            name={item.icon}
            className={cn(
              'shrink-0 transition-colors',
              collapsed ? 'h-[18px] w-[18px]' : 'h-4 w-4',
              isActive ? 'text-brand-600' : 'text-ink-faint group-hover:text-ink-soft'
            )}
          />
          {!collapsed && <span className="truncate">{item.label}</span>}
        </>
      )}
    </NavLink>
  )
}

export function Sidebar({ open, onClose, collapsed, onToggleCollapsed }: Props) {
  const { user, logout } = useAuth()
  const location = useLocation()
  const initial = (user?.email ?? '?').slice(0, 1).toUpperCase()
  const mainItems = filterNav(MAIN_NAV, user?.role)
  const settingsItems = filterNav(SETTINGS_NAV, user?.role)
  const inSettings = location.pathname.startsWith('/settings')

  const accountMenu = (
    <Dropdown
      ariaLabel="账号菜单"
      placement="top"
      align="start"
      panelClassName={cn('w-60', collapsed && 'ml-1')}
      triggerClassName={cn(
        'w-full rounded-xl border border-transparent transition-colors hover:border-line hover:bg-surface-subtle',
        collapsed ? 'justify-center p-2' : 'gap-2.5 p-2'
      )}
      trigger={
        <>
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-xs font-semibold text-white shadow-sm">
            {initial}
          </span>
          {!collapsed && (
            <>
              <span className="min-w-0 flex-1 text-left">
                <span className="block truncate text-ui font-medium text-ink">{user?.email}</span>
                <span className="block text-micro text-ink-faint">
                  {ROLE_LABEL[user?.role ?? 'member'] ?? user?.role}
                </span>
              </span>
              <Icon name="chevron-up" className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
            </>
          )}
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
      <Link to="/settings/general" onClick={onClose}>
        <DropdownItem icon="settings">通用设置</DropdownItem>
      </Link>
      <DropdownItem icon="log-out" tone="danger" onClick={logout}>
        退出登录
      </DropdownItem>
    </Dropdown>
  )

  const body = (isDrawer: boolean) => {
    // 抽屉里永远展开：收起态是桌面省空间的技巧，在手机上只会平白多一层操作
    const isCollapsed = isDrawer ? false : collapsed
    return (
      <>
        <div
          className={cn(
            'flex h-14 shrink-0 items-center border-b border-line/70',
            isCollapsed ? 'justify-center px-2' : 'gap-2 px-3.5'
          )}
        >
          <Link
            to="/"
            onClick={onClose}
            className="flex min-w-0 items-center gap-2.5 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2"
          >
            <LogoMark size={30} title="" />
            {!isCollapsed && (
              <span className="min-w-0">
                <span className="block truncate text-[14px] font-semibold leading-tight tracking-tight text-ink">
                  Agent 工作台
                </span>
                <span className="block truncate text-micro text-ink-faint">
                  Multi-Agent Workspace
                </span>
              </span>
            )}
          </Link>
          {!isCollapsed && (
            <button
              type="button"
              onClick={onToggleCollapsed}
              aria-label="收起侧栏"
              title="收起侧栏"
              className="ml-auto hidden h-8 w-8 items-center justify-center rounded-lg text-ink-faint transition-colors hover:bg-surface-subtle hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 lg:flex"
            >
              <Icon name="chevrons-left" className="h-4 w-4" />
            </button>
          )}
        </div>

        <nav
          aria-label="主导航"
          className={cn('flex-1 overflow-y-auto py-3', isCollapsed ? 'px-2.5' : 'px-2.5')}
        >
          {!isCollapsed && <p className="label-caps mb-1.5 px-2">工作台</p>}
          <div className={cn('flex flex-col gap-0.5', isCollapsed && 'items-center')}>
            {mainItems.map((item) => (
              <NavEntry key={item.to} item={item} collapsed={isCollapsed} onNavigate={onClose} />
            ))}
          </div>

          {settingsItems.length > 0 && (
            <>
              {!isCollapsed && <p className="label-caps mb-1.5 mt-5 px-2">设置</p>}
              {isCollapsed && <div className="my-3 h-px w-6 bg-line" />}
              <div className={cn('flex flex-col gap-0.5', isCollapsed && 'items-center')}>
                <NavLink
                  to="/settings/general"
                  onClick={onClose}
                  title={isCollapsed ? '设置' : undefined}
                  className={navItemClass(isCollapsed)}
                >
                  {({ isActive }) => (
                    <>
                      <span
                        aria-hidden="true"
                        className={cn(
                          'absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-brand-500 transition-opacity',
                          isActive || inSettings ? 'opacity-100' : 'opacity-0',
                          isCollapsed && '-left-1.5'
                        )}
                      />
                      <Icon
                        name="settings"
                        className={cn(
                          'shrink-0',
                          isCollapsed ? 'h-[18px] w-[18px]' : 'h-4 w-4',
                          isActive || inSettings
                            ? 'text-brand-600'
                            : 'text-ink-faint group-hover:text-ink-soft'
                        )}
                      />
                      {!isCollapsed && (
                        <>
                          <span className="truncate">设置</span>
                          <span className="ml-auto rounded-md bg-surface px-1.5 py-0.5 text-micro font-medium text-ink-faint ring-1 ring-inset ring-line/80">
                            {settingsItems.length}
                          </span>
                        </>
                      )}
                    </>
                  )}
                </NavLink>
              </div>
            </>
          )}
        </nav>

        <div
          className={cn(
            'shrink-0 border-t border-line/70',
            isCollapsed ? 'flex flex-col items-center gap-2 p-2.5' : 'p-2.5'
          )}
        >
          {isCollapsed ? (
            <>
              <button
                type="button"
                onClick={onToggleCollapsed}
                aria-label="展开侧栏"
                title="展开侧栏"
                className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-faint transition-colors hover:bg-surface-subtle hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45"
              >
                <Icon name="chevrons-right" className="h-4 w-4" />
              </button>
              {accountMenu}
            </>
          ) : (
            <>
              <div className="mb-2 flex items-center gap-2 rounded-lg bg-surface-muted/80 px-2.5 py-2">
                <span className="relative flex h-2 w-2 shrink-0">
                  <span className="absolute inset-0 animate-ping rounded-full bg-emerald-500/60" />
                  <span className="relative h-2 w-2 rounded-full bg-emerald-500" />
                </span>
                <span className="text-micro font-medium text-ink-soft">系统正常</span>
                <span className="ml-auto font-mono text-micro text-ink-faint">v0.1.0</span>
              </div>
              {accountMenu}
            </>
          )}
        </div>
      </>
    )
  }

  return (
    <>
      {/* 桌面：固定栏 */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 hidden flex-col border-r border-line/70 bg-surface/85 backdrop-blur-xl transition-[width] duration-200 ease-out-expo lg:flex',
          collapsed ? 'w-rail' : 'w-sidebar'
        )}
      >
        {body(false)}
      </aside>

      {/* 移动端：抽屉 + 遮罩 */}
      <div
        className={cn(
          'fixed inset-0 z-50 lg:hidden',
          open ? 'pointer-events-auto' : 'pointer-events-none'
        )}
        aria-hidden={!open}
      >
        <div
          onClick={onClose}
          className={cn(
            'absolute inset-0 backdrop-tint transition-opacity duration-200',
            open ? 'opacity-100' : 'opacity-0'
          )}
        />
        <aside
          role="dialog"
          aria-modal="true"
          aria-label="导航菜单"
          className={cn(
            'absolute inset-y-0 left-0 flex w-[17rem] flex-col border-r border-line bg-surface shadow-pop transition-transform duration-200 ease-out-expo',
            open ? 'translate-x-0' : '-translate-x-full'
          )}
        >
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭菜单"
            className="absolute right-2 top-3.5 z-10 flex h-9 w-9 items-center justify-center rounded-lg text-ink-faint hover:bg-surface-subtle hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45"
          >
            <Icon name="x" className="h-4 w-4" />
          </button>
          {body(true)}
        </aside>
      </div>
    </>
  )
}
