/** 路由与守卫（T026）：登录守卫 + 角色守卫 + 工作台路由（T037/T038）。 */

import { Navigate, Route, Routes } from 'react-router-dom'

import { AppLayout } from './components/layout/AppLayout'
import { useAuth } from './hooks/useAuth'
import { AdminPage } from './pages/AdminPage'
import { AgentPage } from './pages/AgentPage'
import { GroupPage } from './pages/GroupPage'
import { LoginPage } from './pages/LoginPage'
import { RegisterPage } from './pages/RegisterPage'

/** 登录守卫：未登录 → /login。 */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const { initialized, isAuthenticated } = useAuth()
  if (!initialized) {
    return <div className="flex min-h-screen items-center justify-center text-slate-500">加载中...</div>
  }
  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

/** 角色守卫：非 admin → 403 提示。 */
function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useAuth()
  if (!isAdmin) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="rounded-lg bg-red-50 px-6 py-4 text-red-600">
          <p className="font-medium">403 无权限</p>
          <p className="mt-1 text-sm">需要管理员角色才能访问此页面</p>
        </div>
      </div>
    )
  }
  return <>{children}</>
}

/** 工作台首页（Phase 5 替换为 BoardPage）。 */
function BoardHome() {
  const { user } = useAuth()
  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-800">工作台</h1>
      <p className="mt-2 text-slate-600">
        欢迎，{user?.email}（{user?.role}）。从顶部导航管理 Agent 与 Agent 组。
      </p>
    </div>
  )
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
        <Route path="agents" element={<AgentPage />} />
        <Route path="groups" element={<GroupPage />} />
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
