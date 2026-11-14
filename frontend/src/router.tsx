/** 路由与守卫（T026）：登录守卫 + 角色守卫 + 工作台路由（T037/T038）。 */

import { Link, Navigate, Route, Routes } from 'react-router-dom'

import { AppLayout } from './components/layout/AppLayout'
import { Icon } from './components/ui/Icons'
import { LogoMark } from './components/ui/Logo'
import { Spinner } from './components/ui/Spinner'
import { useAuth } from './hooks/useAuth'
import { AdminPage } from './pages/AdminPage'
import { AgentPage } from './pages/AgentPage'
import { BoardPage } from './pages/BoardPage'
import { DashboardPage } from './pages/DashboardPage'
import { GroupPage } from './pages/GroupPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'
import { RunDetailPage } from './pages/RunDetailPage'

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

export function AppRouter() {
  return (
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
        <Route index element={<BoardHome />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="agents" element={<AgentPage />} />
        <Route path="groups" element={<GroupPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route
          path="admin"
          element={
            <RequireAdmin>
              <AdminPage />
            </RequireAdmin>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
