/** 看板（T047 + T056）：状态列 + 筛选 + dnd-kit 拖拽分配。
 *
 * 版式取舍（对齐 Trello / Linear 的看板密度）：
 * - 列宽固定 300px，横向滚动 + 吸附。不再让列「撑满」可用宽度：
 *    五列平分到 1000px 时每列只有 200px，卡片标题必然折成三四行；
 *    固定宽度 + 横向滚动是 Trello 验证过的答案，扫读效率更高。
 * - 「已取消」默认不占一列（终态且通常为空），有数据或主动筛选时才出现；
 * - 列头只留「名称 + 计数」，不用彩色徽章 —— 颜色留给优先级色条，
 *    一列之内再出现五六种彩色标签，眼睛反而找不到重点；
 * - 拖放目标横条放在列上方，见 AssignTargets。
 */

import { DndContext, DragOverlay, DragEndEvent, DragStartEvent } from '@dnd-kit/core'
import { useMemo, useState } from 'react'

import { Agent, Group, WorkRequest } from '../../api/endpoints'
import { IconButton } from '../ui/Button'
import { Icon } from '../ui/Icons'
import { Input } from '../ui/Field'
import { Skeleton } from '../ui/Skeleton'
import { cn } from '../ui/cn'
import { AssignTarget } from './AssignModal'
import { AssignTargets } from './AssignTargets'
import { RequestCard } from './RequestCard'

const COLUMNS: {
  status: WorkRequest['status']
  label: string
  hint: string
}[] = [
  { status: 'created', label: '待分配', hint: '拖到上方目标完成分配' },
  { status: 'running', label: '执行中', hint: 'Agent 正在处理' },
  { status: 'completed', label: '已完成', hint: '执行成功' },
  { status: 'failed', label: '失败', hint: '可重新分配' },
  { status: 'cancelled', label: '已取消', hint: '已终止' },
]

/** 可拖拽（待分配/已分配可重新分配）。 */
const DRAGGABLE_STATUSES = new Set<WorkRequest['status']>(['created', 'assigned'])

const PRIORITY_LABEL: Record<string, string> = {
  high: '高优先级',
  normal: '中优先级',
  low: '低优先级',
}

type Filter = 'all' | WorkRequest['status']

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
  const [filter, setFilter] = useState<Filter>('all')
  const [active, setActive] = useState<WorkRequest | null>(null)

  const filtered = useMemo(() => {
    const like = q.trim().toLowerCase()
    if (!like) return requests
    return requests.filter(
      (r) =>
        r.title.toLowerCase().includes(like) ||
        (r.description ?? '').toLowerCase().includes(like) ||
        (r.expected_output ?? '').toLowerCase().includes(like)
    )
  }, [requests, q])

  const countOf = (status: WorkRequest['status']) =>
    requests.filter((r) => r.status === status).length

  /**
   * 可见列：
   * - 选了具体状态 → 只显示该列；
   * - 全部视图 → 四个工作状态恒定显示，「已取消」只在有数据时出现。
   *   空列在视图里就是白占宽度，而它又极少需要盯着看。
   */
  const visibleColumns =
    filter === 'all'
      ? COLUMNS.filter((c) => c.status !== 'cancelled' || countOf('cancelled') > 0)
      : COLUMNS.filter((c) => c.status === filter)

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

  const chips: { value: Filter; label: string; count: number }[] = [
    { value: 'all', label: '全部', count: requests.length },
    ...COLUMNS.map((c) => ({ value: c.status, label: c.label, count: countOf(c.status) })),
  ]

  return (
    <div className="space-y-3">
      {/* 工具条：左筛选、右搜索。筛选做成带计数的分段控件，
          比下拉少一次点击，也让各状态的积压量直接可见。 */}
      <div className="flex flex-col gap-2.5 lg:flex-row lg:items-center lg:justify-between">
        <div
          role="group"
          aria-label="按状态筛选"
          className="scrollbar-none flex items-center gap-1 overflow-x-auto"
        >
          {chips.map((chip) => {
            const on = filter === chip.value
            return (
              <button
                key={chip.value}
                type="button"
                aria-pressed={on}
                onClick={() => setFilter(chip.value)}
                className={cn(
                  'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border px-2.5 text-ui font-medium transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40',
                  on
                    ? 'border-brand-600 bg-brand-600 text-white'
                    : 'border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink'
                )}
              >
                {chip.label}
                <span className={cn('tabular-nums', on ? 'text-white/75' : 'text-ink-faint')}>
                  {chip.count}
                </span>
              </button>
            )
          })}
        </div>

        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索标题 / 描述 / 期望产出"
          leftIcon="search"
          controlSize="sm"
          aria-label="搜索需求"
          wrapperClassName="w-full lg:w-64"
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
      </div>

      <DndContext onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
        <AssignTargets agents={agents} groups={groups} />

        <div className="relative mt-3">
          <div className="scrollbar-none flex snap-x snap-mandatory items-stretch gap-3 overflow-x-auto pb-2">
            {visibleColumns.map((col) => {
              const items = filtered.filter((r) => r.status === col.status)
              const isRunning = col.status === 'running'
              return (
                <section
                  key={col.status}
                  aria-label={col.label}
                  className="flex w-[84vw] max-w-[340px] shrink-0 snap-start flex-col rounded-xl border border-line/70 bg-surface-subtle/50 p-2 sm:w-[300px] xl:min-w-[300px] xl:flex-1 xl:min-h-[calc(100vh-17rem)]"
                >
                  <header className="-mx-2 mb-2 flex items-center gap-2 border-b border-line/60 px-3.5 pb-2 pt-0.5">
                    {isRunning && (
                      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-sky-500 animate-pulse-ring" />
                    )}
                    <h2 className="text-ui font-semibold text-ink">{col.label}</h2>
                    <span
                      className={cn(
                        'rounded-full px-1.5 text-micro font-medium tabular-nums',
                        items.length > 0
                          ? 'bg-surface text-ink-muted ring-1 ring-inset ring-line'
                          : 'text-ink-faint'
                      )}
                    >
                      {isLoading ? '·' : items.length}
                    </span>
                  </header>

                  <div className="flex flex-1 flex-col gap-2.5">
                    {isLoading ? (
                      <>
                        <Skeleton className="h-[64px] rounded-lg" />
                        <Skeleton className="h-[64px] rounded-lg opacity-60" />
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
                      <div className="flex min-h-[88px] flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-line-strong/70 px-3 text-center">
                        <span className="text-micro font-medium text-ink-faint">暂无需求</span>
                        <span className="text-micro leading-snug text-ink-faint/80">
                          {col.hint}
                        </span>
                      </div>
                    )}
                  </div>
                </section>
              )
            })}
          </div>
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-canvas to-transparent xl:hidden"
          />
        </div>

        {/* 拖拽预览：脱离列容器渲染，避免被裁切/被侧栏遮挡 */}
        <DragOverlay dropAnimation={{ duration: 180, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' }}>
          {active && (
            <div className="w-[268px] -translate-x-1 -translate-y-2 rotate-2 cursor-grabbing rounded-lg border border-brand-300 bg-surface p-3 shadow-pop ring-2 ring-brand-200/60">
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <span className="text-micro font-medium text-ink-muted">
                  {PRIORITY_LABEL[active.priority] ?? active.priority}
                </span>
                <Icon name="grip" className="h-3.5 w-3.5 text-ink-faint" />
              </div>
              <h4 className="line-clamp-2 text-body font-medium leading-snug text-ink">
                {active.title}
              </h4>
              <p className="mt-1.5 text-micro text-ink-faint">松开即可放到目标上</p>
            </div>
          )}
        </DragOverlay>
      </DndContext>
    </div>
  )
}
