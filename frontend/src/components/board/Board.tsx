/** 看板（T047）：按状态分组列 + 关键词搜索框。 */

import { useMemo, useState } from 'react'

import { WorkRequest } from '../../api/endpoints'
import { RequestCard } from './RequestCard'

const COLUMNS: { status: WorkRequest['status']; label: string; color: string }[] = [
  { status: 'created', label: '待分配', color: 'bg-slate-100 text-slate-600' },
  { status: 'running', label: '执行中', color: 'bg-blue-100 text-blue-700' },
  { status: 'completed', label: '已完成', color: 'bg-emerald-100 text-emerald-700' },
  { status: 'failed', label: '失败', color: 'bg-red-100 text-red-700' },
  { status: 'cancelled', label: '已取消', color: 'bg-slate-200 text-slate-500' },
]

interface Props {
  requests: WorkRequest[]
  isLoading?: boolean
}

export function Board({ requests, isLoading }: Props) {
  const [q, setQ] = useState('')

  const filtered = useMemo(() => {
    if (!q.trim()) return requests
    const like = q.trim().toLowerCase()
    return requests.filter(
      (r) =>
        r.title.toLowerCase().includes(like) ||
        (r.description ?? '').toLowerCase().includes(like) ||
        (r.expected_output ?? '').toLowerCase().includes(like),
    )
  }, [requests, q])

  return (
    <div>
      <div className="mb-4">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索需求标题/描述/期望产出..."
          className="w-full max-w-md rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        />
      </div>

      {isLoading ? (
        <div className="py-12 text-center text-slate-500">加载中...</div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {COLUMNS.map((col) => {
            const items = filtered.filter((r) => r.status === col.status)
            return (
              <div key={col.status} className="rounded-xl bg-slate-50 p-3">
                <div className="mb-3 flex items-center justify-between">
                  <span
                    className={`rounded-full px-2.5 py-1 text-xs font-medium ${col.color}`}
                  >
                    {col.label}
                  </span>
                  <span className="text-xs text-slate-400">{items.length}</span>
                </div>
                <div className="space-y-2">
                  {items.map((r) => (
                    <RequestCard key={r.id} request={r} />
                  ))}
                  {items.length === 0 && (
                    <div className="rounded-lg border border-dashed border-slate-200 py-6 text-center text-xs text-slate-400">
                      暂无
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
