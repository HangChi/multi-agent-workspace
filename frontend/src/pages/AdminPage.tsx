/** 管理后台（T026 权限占位 + T079 审计列表）：仅 admin 可访问。 */

import { useEffect, useState } from 'react'

import {
  AuditLogItem,
  fetchAuditLogs,
} from '../api/endpoints'
import { useAuth } from '../hooks/useAuth'
import { ErrorBanner } from '../components/ui/ErrorBanner'

const PAGE_SIZE = 20

/** 时间格式化：YYYY-MM-DD HH:mm:ss（本地时区）。 */
function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

export function AdminPage() {
  const { user } = useAuth()
  const [items, setItems] = useState<AuditLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [actionFilter, setActionFilter] = useState('')
  const [userFilter, setUserFilter] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchAuditLogs({
      user_id: userFilter || undefined,
      action: actionFilter || undefined,
      page,
      page_size: PAGE_SIZE,
    })
      .then((data) => {
        if (cancelled) return
        setItems(data.items)
        setTotal(data.total)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : '加载审计日志失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [page, actionFilter, userFilter])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  /** 应用筛选：重置到第一页。 */
  function applyFilter() {
    setPage(1)
  }

  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold text-slate-800">管理后台</h1>
      <p className="mt-2 text-slate-600">
        当前身份：<span className="font-medium">{user?.email}</span>（
        <span className="text-blue-600">{user?.role}</span>）
      </p>

      {/* 筛选栏 */}
      <div className="mt-6 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-slate-500" htmlFor="audit-user">
            用户 ID
          </label>
          <input
            id="audit-user"
            value={userFilter}
            onChange={(e) => setUserFilter(e.target.value)}
            placeholder="按用户 ID 筛选"
            className="mt-1 w-56 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-slate-500" htmlFor="audit-action">
            动作
          </label>
          <input
            id="audit-action"
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            placeholder="如 user_login / agent.create"
            className="mt-1 w-64 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
          />
        </div>
        <button
          type="button"
          onClick={applyFilter}
          className="rounded-md bg-slate-800 px-4 py-1.5 text-sm text-white hover:bg-slate-700"
        >
          筛选
        </button>
        <button
          type="button"
          onClick={() => {
            setUserFilter('')
            setActionFilter('')
            setPage(1)
          }}
          className="rounded-md border border-slate-300 px-4 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
        >
          清空
        </button>
        <span className="ml-auto text-sm text-slate-500">
          共 <span className="font-medium">{total}</span> 条
        </span>
      </div>

      {error && (
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      )}

      {/* 审计列表 */}
      <div className="mt-4 overflow-x-auto rounded-lg border border-slate-200">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="px-4 py-2 text-left font-medium text-slate-500">时间</th>
              <th className="px-4 py-2 text-left font-medium text-slate-500">用户</th>
              <th className="px-4 py-2 text-left font-medium text-slate-500">动作</th>
              <th className="px-4 py-2 text-left font-medium text-slate-500">目标</th>
              <th className="px-4 py-2 text-left font-medium text-slate-500">详情</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && items.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                  加载中…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                  暂无审计日志
                </td>
              </tr>
            ) : (
              items.map((item) => (
                <tr key={item.id} className="hover:bg-slate-50">
                  <td className="whitespace-nowrap px-4 py-2 text-slate-500">
                    {formatTime(item.created_at)}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-slate-600">
                    {item.user_id.slice(0, 8)}…
                  </td>
                  <td className="px-4 py-2">
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-700">
                      {item.action}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-xs text-slate-500">
                    {item.target_type ? `${item.target_type}${item.target_id ? `:${item.target_id.slice(0, 8)}…` : ''}` : '—'}
                  </td>
                  <td className="max-w-xs truncate px-4 py-2 font-mono text-xs text-slate-500">
                    {item.detail ? JSON.stringify(item.detail) : '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* 分页 */}
      {total > PAGE_SIZE && (
        <div className="mt-4 flex items-center gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-600 disabled:opacity-40"
          >
            上一页
          </button>
          <span className="text-sm text-slate-500">
            第 {page} / {totalPages} 页
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-600 disabled:opacity-40"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
