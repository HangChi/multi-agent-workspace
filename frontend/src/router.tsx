/** 路由与守卫（T026）：登录守卫 + 角色守卫。 */

import { Navigate, Route, Routes } from 'react-router-dom'

import { useAuth } from './hooks/useAuth'
import { AdminPage } from './pages/AdminPage'
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

/** 工作台占位（后续 Phase 替换为 BoardPage）。 */
function BoardPlaceholder() {
  const { user } = useAuth()
  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold text-slate-800">工作台</h1>
      <p className="mt-2 text-slate-600">欢迎，{user?.email}（{user?.role}）</p>
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
            <BoardPlaceholder />
          </RequireAuth>
        }
      />
      <Route
        path="/admin"
        element={
          <RequireAuth>
            <RequireAdmin>
              <AdminPage />
            </RequireAdmin>
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
