/** 看板（T047 + T056）：按状态分组列 + 搜索 + dnd-kit 拖拽分配。 */

import { DndContext, DragEndEvent, DragStartEvent } from '@dnd-kit/core'
import { useMemo, useState } from 'react'

import { Agent, Group, WorkRequest } from '../../api/endpoints'
import { AssignTarget } from './AssignModal'
import { AssignTargets } from './AssignTargets'
import { RequestCard } from './RequestCard'

const COLUMNS: { status: WorkRequest['status']; label: string; color: string }[] = [
  { status: 'created', label: '待分配', color: 'bg-slate-100 text-slate-600' },
  { status: 'running', label: '执行中', color: 'bg-blue-100 text-blue-700' },
  { status: 'completed', label: '已完成', color: 'bg-emerald-100 text-emerald-700' },
  { status: 'failed', label: '失败', color: 'bg-red-100 text-red-700' },
  { status: 'cancelled', label: '已取消', color: 'bg-slate-200 text-slate-500' },
]

/** 可拖拽（待分配/已分配可重新分配）。 */
const DRAGGABLE_STATUSES = new Set<WorkRequest['status']>(['created', 'assigned'])

interface Props {
  requests: WorkRequest[]
  agents: Agent[]
  groups: Group[]
  isLoading?: boolean
  /** 拖拽落点回调（触发分配弹窗）。 */
  onAssignStart: (request: WorkRequest, target: AssignTarget) => void
}

export function Board({ requests, agents, groups, isLoading, onAssignStart }: Props) {
  const [q, setQ] = useState('')
  const [active, setActive] = useState<WorkRequest | null>(null)

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

  const handleDragStart = (e: DragStartEvent) => {
    const id = String(e.active.id).replace(/^request:/, '')
    setActive(requests.find((r) => r.id === id) ?? null)
  }

  const handleDragEnd = (e: DragEndEvent) => {
    const over = e.over
    const activeReq = active
    setActive(null)
    if (!over || !activeReq) return
    const overId = String(over.id)
    // 目标 id 形如 agent:<uuid> / group:<uuid>
    if (overId.startsWith('agent:')) {
      onAssignStart(activeReq, { kind: 'agent', id: overId.slice('agent:'.length) })
    } else if (overId.startsWith('group:')) {
      onAssignStart(activeReq, { kind: 'group', id: overId.slice('group:'.length) })
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-start justify-between gap-6">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索需求标题/描述/期望产出..."
          className="w-full max-w-md rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        />
        <span className="shrink-0 text-xs text-slate-400">
          拖拽「待分配/已分配」卡片到右侧目标
        </span>
      </div>

      <DndContext onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
        <div className="flex items-start gap-4">
          <div className="grid min-w-0 flex-1 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {isLoading ? (
              <div className="py-12 text-center text-slate-500">加载中...</div>
            ) : (
              COLUMNS.map((col) => {
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
                        <RequestCard
                          key={r.id}
                          request={r}
                          draggable={DRAGGABLE_STATUSES.has(r.status)}
                        />
                      ))}
                      {items.length === 0 && (
                        <div className="rounded-lg border border-dashed border-slate-200 py-6 text-center text-xs text-slate-400">
                          暂无
                        </div>
                      )}
                    </div>
                  </div>
                )
              })
            )}
          </div>

          <AssignTargets agents={agents} groups={groups} />
        </div>
      </DndContext>
    </div>
  )
}
