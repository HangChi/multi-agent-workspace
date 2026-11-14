/** 管理后台（T026 权限占位页）：仅 admin 可访问。 */

import { useAuth } from '../hooks/useAuth'

export function AdminPage() {
  const { user } = useAuth()

  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold text-slate-800">管理后台</h1>
      <p className="mt-2 text-slate-600">
        当前身份：<span className="font-medium">{user?.email}</span>（
        <span className="text-blue-600">{user?.role}</span>）
      </p>
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-slate-200 p-4">
          <h2 className="font-medium text-slate-800">审计日志</h2>
          <p className="mt-1 text-sm text-slate-500">查看系统敏感操作记录（Phase 8 接入）</p>
        </div>
        <div className="rounded-lg border border-slate-200 p-4">
          <h2 className="font-medium text-slate-800">全局配置</h2>
          <p className="mt-1 text-sm text-slate-500">团队配额与运行参数（Phase 8 接入）</p>
        </div>
      </div>
    </div>
  )
}
