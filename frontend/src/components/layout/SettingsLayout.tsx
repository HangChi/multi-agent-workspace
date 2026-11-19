/**
 * 设置模块外壳：左侧二级导航 + 右侧内容。
 *
 * 为什么用「设置里再嵌一层导航」而不是几个平级页面：
 * 模板 / 调度 / 工具市场 / 管理后台都是低频配置项，彼此之间不需要快速来回，
 * 但需要被放在同一个心智位置 ——「我在改系统的配置」。
 *
 * 窄屏退化为横向可滚动 chip 条：不用为了切一个设置页再开一次抽屉。
 */

import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'
import { SETTINGS_NAV, activeSettingsItem, filterNav } from './nav'

export function SettingsLayout() {
  const { user } = useAuth()
  const location = useLocation()
  const items = filterNav(SETTINGS_NAV, user?.role)
  const current = activeSettingsItem(location.pathname)

  return (
    <div className="flex flex-col gap-5 lg:flex-row lg:gap-8">
      <aside className="lg:w-56 lg:shrink-0">
        <div className="lg:sticky lg:top-[5.25rem]">
          <p className="label-caps mb-2 hidden px-2.5 lg:block">设置</p>

          {/* 桌面：竖向列表。条目只放标题——描述文字统一由下方的
              虚线框承担（当前项），避免同屏出现两遍同一句话。
              激活态用品牌淡蓝底 + 左侧 2px 品牌条：竖向导航的「你在哪」
              需要最强的视觉锚点，白底 + 灰环在灰画布上几乎看不见。 */}
          <nav aria-label="设置导航" className="hidden flex-col gap-0.5 lg:flex">
            {items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'group relative flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] transition-all duration-150',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2',
                    isActive
                      ? 'bg-surface-brand font-semibold text-accent ring-1 ring-inset ring-brand-200/60 dark:ring-brand-500/20'
                      : 'text-ink-soft hover:bg-surface/70 hover:text-ink'
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive && (
                      <span
                        aria-hidden="true"
                        className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-gradient-to-b from-brand-400 to-brand-600"
                      />
                    )}
                    <Icon
                      name={item.icon}
                      className={cn(
                        'h-4 w-4 shrink-0',
                        isActive ? 'text-brand-600 dark:text-brand-300' : 'text-ink-faint'
                      )}
                    />
                    <span className="min-w-0 truncate text-ui font-medium">
                      {item.label}
                    </span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          {/* 移动端：横向 chip 条，边缘渐隐提示可滑 */}
          <div className="relative lg:hidden">
            <nav
              aria-label="设置导航（移动端）"
              className="scrollbar-none -mx-4 flex gap-1.5 overflow-x-auto px-4 py-1 sm:-mx-6 sm:px-6"
            >
              {items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      'inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border px-3 text-ui font-medium transition-colors',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45',
                      isActive
                        ? 'border-brand-200 bg-brand-50 text-accent'
                        : 'border-line bg-surface text-ink-soft'
                    )
                  }
                >
                  <Icon name={item.icon} className="h-3.5 w-3.5" />
                  {item.shortLabel ?? item.label}
                </NavLink>
              ))}
            </nav>
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-canvas to-transparent"
            />
          </div>

          {current?.description && (
            <p className="mt-4 hidden rounded-lg border border-dashed border-line px-2.5 py-2 text-micro leading-relaxed text-ink-faint lg:block">
              {current.description}
            </p>
          )}
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <Outlet />
      </div>
    </div>
  )
}
