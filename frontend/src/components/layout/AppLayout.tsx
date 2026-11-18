/**
 * 工作台布局：顶部横向导航 + 内容区。
 *
 * 2026-11-18：从「侧栏 + 顶栏」改回横栏。侧栏常驻占 256px，而这个工作台
 * 几乎全是宽内容页面（看板 / 列表 / 表格），横栏能把宽度还给内容；
 * 同时模块已靠「设置」收敛成 5 个入口，横栏不会再出现 8 项挤一排的老问题。
 *
 * 右上角不放「系统正常」pill（无探活背书、且与设置里的信息重复），
 * 只保留 ⌘K、明/暗切换、个人菜单；版本号在设置 → 通用设置。
 */

import { Outlet, useLocation } from 'react-router-dom'

import { ErrorBoundary } from '../ui/ErrorBoundary'
import { ContentShell, Topbar } from './Topbar'

export function AppLayout() {
  // 用 location.key 作错误边界的复位信号：切页面时自动清掉上一页的渲染错误，
  // 否则用户修好数据回到该页仍是错误状态（错误边界只在 key 变化时复位）。
  const location = useLocation()

  return (
    <div className="relative flex min-h-screen flex-col bg-canvas">
      {/* 氛围层：固定不滚动，只在首屏上方淡出，给白卡片一个不扁平的落点 */}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-x-0 top-0 h-[30rem] canvas-atmosphere"
      />

      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-skip focus:rounded-lg focus:bg-brand-600 focus:px-4 focus:py-2.5 focus:text-sm focus:font-medium focus:text-white"
      >
        跳到主内容
      </a>

      <Topbar />

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
        <ContentShell className="py-4 text-micro text-ink-faint">
          Agent 工作台 · 内部协作平台
        </ContentShell>
      </footer>
    </div>
  )
}
