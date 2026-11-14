/** 工作台布局：顶部导航 + 内容区（T037/T038 共用）。 */

import { Link, NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '../../hooks/useAuth'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
    isActive
      ? 'bg-blue-100 text-blue-700'
      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
  }`

export function AppLayout() {
  const { user, logout } = useAuth()

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <Link to="/" className="text-lg font-bold text-slate-800">
              Agent 工作台
            </Link>
            <nav className="flex items-center gap-1">
              <NavLink to="/" end className={navClass}>
                看板
              </NavLink>
              <NavLink to="/agents" className={navClass}>
                Agent
              </NavLink>
              <NavLink to="/groups" className={navClass}>
                Agent 组
              </NavLink>
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-sm text-slate-500">
              {user?.email}（{user?.role}）
            </span>
            <button
              onClick={logout}
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
            >
              退出
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
