/** 需求卡片（T047 + T056）：看板上的最小单元，可拖拽分配给 Agent/组。
 *
 * 信息层级（从上到下）：优先级 → 标题 → 摘要 → 元信息 / 操作。
 * 卡片高度不做强制统一：需求短就矮，扫读时反而更容易分辨。
 */

import { useDraggable } from '@dnd-kit/core'
import { Link } from 'react-router-dom'

import { WorkRequest } from '../../api/endpoints'
import { useBoardStore } from '../../stores/boardStore'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

const PRIORITY_LABEL: Record<string, string> = {
  high: '高',
  normal: '中',
  low: '低',
}

/** 卡片左侧的优先级色条，比整块染色更克制 */
const PRIORITY_ACCENT: Record<string, string> = {
  high: 'bg-rose-400',
  normal: 'bg-brand-400',
  low: 'bg-line-strong',
}

/** 优先级标签的浅色底：只染底与字，不加边框，避免卡片上出现三层框线 */
const PRIORITY_CHIP: Record<string, string> = {
  high: 'bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300',
  normal: 'bg-brand-50 text-accent dark:bg-brand-500/10',
  low: 'bg-surface-subtle text-ink-muted',
}

interface Props {
  request: WorkRequest
  /** 是否可拖拽（仅待分配/已分配可分配）。 */
  draggable?: boolean
  /** 打开详情弹层 */
  onOpen?: () => void
}

export function RequestCard({ request, draggable = true, onOpen }: Props) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `request:${request.id}`,
    data: { kind: 'request', id: request.id, title: request.title },
    disabled: !draggable,
  })

  const runId = useBoardStore((s) => s.runByRequest[request.id])

  // 拖拽时原卡片退化为「占位残影」，真正的预览由 Board 的 DragOverlay 渲染，
  // 避免被列容器裁切、也不会和侧栏重叠。
  const style = { opacity: isDragging ? 0.35 : 1 }

  /** 卡片内的按钮/链接不参与拖拽，避免「想点却拖起来」。 */
  const stopDrag = (e: React.PointerEvent) => e.stopPropagation()

  return (
    <article
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      className={cn(
        'group relative overflow-hidden rounded-lg border border-line/90 bg-surface shadow-card transition-all duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-1',
        isDragging
          ? 'cursor-grabbing border-dashed border-brand-300 bg-brand-50/40 opacity-60 shadow-none'
          : draggable
            ? 'cursor-grab hover:-translate-y-0.5 hover:border-line-strong hover:shadow-panel'
            : 'hover:border-line-strong'
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-y-0 left-0 w-[3px]',
          PRIORITY_ACCENT[request.priority] ?? 'bg-line-strong'
        )}
      />

      <div className="py-2.5 pl-3.5 pr-2.5">
        <div className="mb-1.5 flex items-center gap-1.5">
          <span
            className={cn(
              'inline-flex items-center rounded px-1.5 py-px text-[10px] font-semibold',
              PRIORITY_CHIP[request.priority] ?? PRIORITY_CHIP.low
            )}
            title={`优先级：${PRIORITY_LABEL[request.priority] ?? request.priority}`}
          >
            {PRIORITY_LABEL[request.priority] ?? request.priority}
          </span>

          {/* 来源徽标放在卡片上而不是只在详情里：看板上要能一眼分辨
              「人建的」和「规则建的」，否则一堆自动需求看起来像是谁手动拖进来的 */}
          {request.source_schedule_id && (
            <span
              className="inline-flex items-center gap-0.5 rounded bg-surface-subtle px-1 py-px text-[10px] font-medium text-ink-muted"
              title={
                request.source_schedule_name
                  ? `来自调度「${request.source_schedule_name}」`
                  : '由调度自动创建'
              }
            >
              <Icon name="clock" className="h-2.5 w-2.5" />
              定时
            </span>
          )}

          <span className="ml-auto text-[10px] tabular-nums text-ink-faint">
            {new Date(request.created_at).toLocaleDateString('zh-CN', {
              month: '2-digit',
              day: '2-digit',
            })}
          </span>
          {draggable && (
            <Icon
              name="grip"
              className="h-3.5 w-3.5 shrink-0 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100"
            />
          )}
        </div>

        <h4 className="line-clamp-2 text-[13px] font-semibold leading-snug text-ink">
          {request.title}
        </h4>
        {request.description && (
          <p className="mt-1 line-clamp-2 text-[11.5px] leading-relaxed text-ink-muted">
            {request.description}
          </p>
        )}

        <div className="mt-2 flex items-center gap-2">
          {request.attachments.length > 0 && (
            <span
              className="inline-flex items-center gap-0.5 text-[10px] text-ink-faint"
              title={`${request.attachments.length} 个附件`}
            >
              <Icon name="paperclip" className="h-3 w-3" />
              {request.attachments.length}
            </span>
          )}

          {runId ? (
            <Link
              to={`/runs/${runId}`}
              onPointerDown={stopDrag}
              className="inline-flex items-center gap-1 rounded bg-brand-50 px-1.5 py-0.5 text-[10px] font-medium text-accent transition-colors hover:bg-brand-100 dark:bg-brand-500/10 dark:hover:bg-brand-500/20"
            >
              <Icon name="play" className="h-2.5 w-2.5" />
              查看运行
            </Link>
          ) : (
            draggable && (
              <span className="text-[10px] text-ink-faint opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                拖拽分配
              </span>
            )
          )}

          {onOpen && (
            <button
              type="button"
              onPointerDown={stopDrag}
              onClick={(e) => {
                e.stopPropagation()
                onOpen()
              }}
              className="ml-auto rounded px-1.5 py-0.5 text-[10px] font-medium text-ink-muted transition-colors hover:bg-surface-subtle hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40"
            >
              详情
            </button>
          )}
        </div>
      </div>
    </article>
  )
}
