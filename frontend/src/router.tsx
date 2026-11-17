/** 路由与守卫（T026）：登录守卫 + 角色守卫 + 工作台路由（T037/T038）。 */

import { Suspense, lazy } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'

import { AppLayout } from './components/layout/AppLayout'
import { Icon } from './components/ui/Icons'
import { LogoMark } from './components/ui/Logo'
import { Spinner } from './components/ui/Spinner'
import { useAuth } from './hooks/useAuth'

/**
 * 路由级代码分割。
 *
 * 此前所有页面都静态 import，单个 480KB / gzip 153KB 的包必须整包下载才能
 * 首屏可见。拆开后首屏只需要「登录/布局/看板」这条主路径，其余按需拉取。
 *
 * 特别注意 BoardPage：它引入了 @dnd-kit（约 113KB 源码，占全包 8.8%），
 * 而登录页完全用不到 —— 不拆的话未登录用户也要付这份下载代价。
 *
 * ⚠️ React.lazy 的默认导出要求：用 `.then(m => ({ default: m.XxxPage }))`
 * 适配命名导出，别为了偷懒去改各页面的导出形式。
 */
const LoginPage = lazy(() =>
  import('./pages/LoginPage').then((m) => ({ default: m.LoginPage })),
)
const RegisterPage = lazy(() =>
  import('./pages/RegisterPage').then((m) => ({ default: m.RegisterPage })),
)
const BoardPage = lazy(() =>
  import('./pages/BoardPage').then((m) => ({ default: m.BoardPage })),
)
const DashboardPage = lazy(() =>
  import('./pages/DashboardPage').then((m) => ({ default: m.DashboardPage })),
)
const AgentPage = lazy(() =>
  import('./pages/AgentPage').then((m) => ({ default: m.AgentPage })),
)
const GroupPage = lazy(() =>
  import('./pages/GroupPage').then((m) => ({ default: m.GroupPage })),
)
const MarketplacePage = lazy(() =>
  import('./pages/MarketplacePage').then((m) => ({ default: m.MarketplacePage })),
)
const RunDetailPage = lazy(() =>
  import('./pages/RunDetailPage').then((m) => ({ default: m.RunDetailPage })),
)
const AdminPage = lazy(() =>
  import('./pages/AdminPage').then((m) => ({ default: m.AdminPage })),
)

/** 全屏加载：恢复会话期间别让用户看到白屏。 */
function FullPageLoading() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-canvas">
      <LogoMark size={44} />
      <div className="flex items-center gap-2 text-sm text-ink-muted">
        <Spinner className="h-3.5 w-3.5 text-brand-500" />
        正在恢复会话…
      </div>
    </div>
  )
}

/**
 * 懒加载页面加载态。
 *
 * 用 min-h 撑住高度而不是只放个尾巴 spinner：否则内容区会从 0 高度跳到
 * 满屏，滚动条闪一下，体感比等待本身更差。
 */
function PageLoading() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
      <Spinner className="h-5 w-5 text-brand-500" />
      <p className="text-sm text-ink-muted">加载中…</p>
    </div>
  )
}

/** 登录守卫：未登录 → /login。 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { initialized, isAuthenticated } = useAuth()
  if (!initialized) return <FullPageLoading />
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

/** 角色守卫：非 admin → 403 提示。 */
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useAuth()
  if (!isAdmin) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-2xl border border-line bg-surface p-6 text-center shadow-card">
          <span className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-rose-50 text-rose-500 ring-1 ring-inset ring-rose-100">
            <Icon name="shield" className="h-5 w-5" />
          </span>
          <p className="text-base font-semibold text-ink">403 · 无访问权限</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-muted">
            该页面仅限管理员角色访问，如需权限请联系系统管理员。
          </p>
          <Link
            to="/"
            className="mt-4 inline-flex h-9 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 text-sm font-medium text-ink-soft shadow-card transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45"
          >
            <Icon name="arrow-left" className="h-4 w-4" />
            返回看板
          </Link>
        </div>
      </div>
    )
  }
  return <>{children}</>
}

/** 工作台首页（Phase 6 起为拖拽看板交互入口）。 */
function BoardHome() {
  return <BoardPage />
}

/**
 * 路由出口的统一 Suspense 边界。
 *
 * 放在 <Routes> 外层而不是每个 element 上：翻页时 Suspense 的 fallback 会
 * 直接替掉旧内容，若边界在路由内侧，滚动位置和布局容器会被反复重建。
 * 放在外层则 AppLayout（导航栏）保持挂载，只有内容区变化。
 */
export function AppRouter() {
  return (
    <Suspense fallback={<FullPageLoading />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          {/* Outlet 内部单独套一层：这样布局内翻页只显示内容区加载态，
              不会把已渲染的导航栏一起换成全屏 loading。 */}
          <Route index element={<Suspended element={<BoardHome />} />} />
          <Route path="dashboard" element={<Suspended element={<DashboardPage />} />} />
          <Route path="agents" element={<Suspended element={<AgentPage />} />} />
          <Route path="groups" element={<Suspended element={<GroupPage />} />} />
          <Route
            path="marketplace"
            element={<Suspended element={<MarketplacePage />} />}
          />
          <Route path="runs/:runId" element={<Suspended element={<RunDetailPage />} />} />
          <Route
            path="admin"
            element={
              <RequireAdmin>
                <Suspended element={<AdminPage />} />
              </RequireAdmin>
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}

/** 区块级 Suspense：懒加载页面在布局内切换时只替换内容区。 */
function Suspended({ element }: { element: React.ReactNode }) {
  return <Suspense fallback={<PageLoading />}>{element}</Suspense>
}
