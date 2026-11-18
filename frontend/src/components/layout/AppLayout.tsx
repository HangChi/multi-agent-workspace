/**
 * 工作台布局：固定侧栏 + 顶栏 + 内容区。
 *
 * 与旧版的差别（也是这次重排的核心）：
 * - 顶部横向导航放不下 8 个模块了，改成侧栏：主导航 4 项 +「设置」1 项；
 * - 模板 / 调度 / 工具市场 / 管理后台收进设置模块（见 SettingsLayout）；
 * - 内容区左偏移跟随侧栏收起态，桌面省下 100+px 给看板列；
 * - 背景加一层固定的冷色渐晕 + 点阵，滚动时不动，避免大片死白。
 */

import { useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'

import { ErrorBoundary } from '../ui/ErrorBoundary'
import { cn } from '../ui/cn'
import { Sidebar } from './Sidebar'
import { ContentShell, Topbar } from './Topbar'

const COLLAPSE_KEY = 'aw_sidebar_collapsed'

export function AppLayout() {
  // 用 location.key 作错误边界的复位信号：切页面时自动清掉上一页的渲染错误，
  // 否则用户修好数据回到该页仍是错误状态（错误边界只在 key 变化时复位）。
  const location = useLocation()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.localStorage.getItem(COLLAPSE_KEY) === '1'
  })

  useEffect(() => {
    window.localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0')
  }, [collapsed])

  // 路由变化自动关抽屉：否则点完菜单它还会盖在新页面上
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  // 抽屉打开时锁滚动，并支持 ESC 关闭
  useEffect(() => {
    if (!drawerOpen) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawerOpen(false)
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = prev
      document.removeEventListener('keydown', onKey)
    }
  }, [drawerOpen])

  return (
    <div className="relative min-h-screen bg-canvas">
      {/* 氛围层：固定不滚动，只在首屏上方淡出，给白卡片一个不扁平的落点 */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-x-0 top-0 h-[30rem] canvas-atmosphere"
      />

      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded-lg focus:bg-brand-600 focus:px-4 focus:py-2.5 focus:text-sm focus:font-medium focus:text-white"
      >
        跳到主内容
      </a>

      <Sidebar
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed((v) => !v)}
      />

      <div
        className={cn(
          'relative flex min-h-screen flex-col transition-[padding-left] duration-200 ease-out-expo',
          collapsed ? 'lg:pl-rail' : 'lg:pl-sidebar'
        )}
      >
        <Topbar pathname={location.pathname} onOpenMenu={() => setDrawerOpen(true)} />

        <main id="main-content" tabIndex={-1} className="flex-1 focus-visible:outline-none">
          <ContentShell>
            {/* 区块级兜底：某个页面渲染崩溃时，导航栏仍可用，用户能切走。
                根级兜底在 App.tsx，两层互补。 */}
            <ErrorBoundary variant="section" resetKey={location.key}>
              <Outlet />
            </ErrorBoundary>
          </ContentShell>
        </main>

        <footer className="mt-auto border-t border-line/60">
          <ContentShell className="flex flex-col gap-1 py-4 text-micro text-ink-faint sm:flex-row sm:items-center sm:justify-between">
            <span>Agent 工作台 · 内部协作平台</span>
            <span className="font-mono">v0.1.0 · {new Date().getFullYear()}</span>
          </ContentShell>
        </footer>
      </div>
    </div>
  )
}
