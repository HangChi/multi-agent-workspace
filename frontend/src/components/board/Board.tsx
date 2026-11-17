/** 看板（T047 + T056）：按状态分组列 + 搜索 + dnd-kit 拖拽分配。 */

import { DndContext, DragOverlay, DragEndEvent, DragStartEvent } from '@dnd-kit/core'
import { useMemo, useState } from 'react'

import { Agent, Group, WorkRequest } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { IconButton } from '../ui/Button'
import { Icon } from '../ui/Icons'
import { Input } from '../ui/Field'
import { Skeleton } from '../ui/Skeleton'
import { AssignTarget } from './AssignModal'
import { AssignTargets } from './AssignTargets'
import { RequestCard } from './RequestCard'

const COLUMNS: {
  status: WorkRequest['status']
  label: string
  tone: BadgeTone
  hint: string
}[] = [
  { status: 'created', label: '待分配', tone: 'slate', hint: '拖到右侧分配' },
  { status: 'running', label: '执行中', tone: 'sky', hint: 'Agent 正在处理' },
  { status: 'completed', label: '已完成', tone: 'emerald', hint: '执行成功' },
  { status: 'failed', label: '失败', tone: 'rose', hint: '可重新分配' },
  { status: 'cancelled', label: '已取消', tone: 'slate', hint: '已终止' },
]

/** 可拖拽（待分配/已分配可重新分配）。 */
const DRAGGABLE_STATUSES = new Set<WorkRequest['status']>(['created', 'assigned'])

const PRIORITY_LABEL: Record<string, string> = {
  high: '高优先级',
  normal: '中优先级',
  low: '低优先级',
}

interface Props {
  requests: WorkRequest[]
  agents: Agent[]
  groups: Group[]
  isLoading?: boolean
  /** 拖拽落点回调（触发分配弹窗）。 */
  onAssignStart: (request: WorkRequest, target: AssignTarget) => void
  /** 打开需求详情 */
  onOpenDetail?: (request: WorkRequest) => void
}

export function Board({ requests, agents, groups, isLoading, onAssignStart, onOpenDetail }: Props) {
  const [q, setQ] = useState('')
  const [active, setActive] = useState<WorkRequest | null>(null)

  const filtered = useMemo(() => {
    if (!q.trim()) return requests
    const like = q.trim().toLowerCase()
    return requests.filter(
      (r) =>
        r.title.toLowerCase().includes(like) ||
        (r.description ?? '').toLowerCase().includes(like) ||
        (r.expected_output ?? '').toLowerCase().includes(like)
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
      {/* 工具条 */}
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索标题 / 描述 / 期望产出"
            leftIcon="search"
            controlSize="sm"
            aria-label="搜索需求"
            wrapperClassName="w-full sm:w-72"
            rightSlot={
              q ? (
                <IconButton
                  icon="x"
                  label="清除搜索"
                  size="xs"
                  onClick={() => setQ('')}
                  className="mr-0.5"
                />
              ) : undefined
            }
          />
          {q && (
            <Badge tone="brand" className="animate-fade-in">
              {filtered.length} / {requests.length}
            </Badge>
          )}
        </div>
        <p className="flex items-center gap-1.5 text-xs text-ink-faint">
          <Icon name="info" className="h-3.5 w-3.5" />
          将「待分配」卡片拖到右侧 Agent 或组上完成分配
        </p>
      </div>

      <DndContext onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start">
          <div className="min-w-0 flex-1">
            {/* 窄屏下列表横向滚动，右侧渐隐提示「还有列」 */}
            <div className="relative">
              <div className="scrollbar-none flex gap-3 overflow-x-auto pb-1">
                {COLUMNS.map((col) => {
                  const items = filtered.filter((r) => r.status === col.status)
                  return (
                    <section
                      key={col.status}
                      aria-label={col.label}
                      className="flex w-[248px] shrink-0 flex-col rounded-xl border border-line/70 bg-surface-subtle/50 p-2 sm:w-[262px] xl:min-h-[420px] xl:w-auto xl:min-w-0 xl:flex-1"
                    >
                      <header className="mb-2 flex items-center justify-between gap-2 px-1 py-0.5">
                        <div className="flex min-w-0 items-center gap-1.5">
                          <Badge tone={col.tone} dot pulse={col.status === 'running'}>
                            {col.label}
                          </Badge>
                        </div>
                        <span className="shrink-0 rounded-full bg-surface px-1.5 py-0.5 text-2xs font-medium tabular-nums text-ink-muted ring-1 ring-inset ring-line/80">
                          {isLoading ? '·' : items.length}
                        </span>
                      </header>

                      <div className="space-y-2">
                        {isLoading ? (
                          <>
                            <Skeleton className="h-[86px] rounded-lg" />
                            <Skeleton className="h-[86px] rounded-lg opacity-60" />
                          </>
                        ) : (
                          items.map((r) => (
                            <RequestCard
                              key={r.id}
                              request={r}
                              draggable={DRAGGABLE_STATUSES.has(r.status)}
                              onOpen={onOpenDetail ? () => onOpenDetail(r) : undefined}
                            />
                          ))
                        )}

                        {!isLoading && items.length === 0 && (
                          <div className="flex h-20 flex-col items-center justify-center gap-0.5 rounded-lg border border-dashed border-line-strong/80 text-center">
                            <span className="text-2xs text-ink-faint">暂无需求</span>
                            <span className="text-[10px] text-ink-faint">{col.hint}</span>
                          </div>
                        )}
                      </div>
                    </section>
                  )
                })}
              </div>
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-y-0 right-0 w-12 bg-gradient-to-l from-canvas via-canvas/70 to-transparent xl:hidden"
              />
            </div>
          </div>

          <AssignTargets agents={agents} groups={groups} />
        </div>

        {/* 拖拽预览：脱离列容器渲染，避免被裁切/被侧栏遮挡 */}
        <DragOverlay dropAnimation={{ duration: 180, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' }}>
          {active && (
            <div className="w-[232px] -translate-x-1 -translate-y-2 rotate-2 cursor-grabbing rounded-lg border border-brand-300 bg-surface p-3 shadow-pop ring-2 ring-brand-200/60">
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <Badge
                  tone={
                    active.priority === 'high'
                      ? 'rose'
                      : active.priority === 'low'
                        ? 'slate'
                        : 'brand'
                  }
                  className="px-1.5 py-0"
                >
                  {PRIORITY_LABEL[active.priority] ?? active.priority}
                </Badge>
                <Icon name="grip" className="h-3.5 w-3.5 text-ink-faint" />
              </div>
              <h4 className="line-clamp-2 text-[13px] font-semibold leading-snug text-ink">
                {active.title}
              </h4>
              <p className="mt-1.5 text-2xs text-ink-faint">松开即可放到目标上</p>
            </div>
          )}
        </DragOverlay>
      </DndContext>
    </div>
  )
}
