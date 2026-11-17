/** 需求卡片（T047 + T056）：看板上的最小单元，可拖拽分配给 Agent/组。 */

import { useDraggable } from '@dnd-kit/core'
import { Link } from 'react-router-dom'

import { WorkRequest } from '../../api/endpoints'
import { useBoardStore } from '../../stores/boardStore'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

const PRIORITY_TONE: Record<string, BadgeTone> = {
  high: 'rose',
  normal: 'brand',
  low: 'slate',
}

const PRIORITY_LABEL: Record<string, string> = {
  high: '高优先级',
  normal: '中优先级',
  low: '低优先级',
}

/** 卡片左侧的优先级色条，比整块染色更克制 */
const PRIORITY_ACCENT: Record<string, string> = {
  high: 'bg-rose-400',
  normal: 'bg-brand-400',
  low: 'bg-line-strong',
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
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      className={cn(
        'group relative rounded-lg border border-line/90 bg-surface p-3 pl-3.5 shadow-card transition-all duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-1',
        isDragging
          ? 'cursor-grabbing border-dashed border-brand-300 bg-brand-50/40 opacity-60 shadow-none'
          : draggable
            ? 'cursor-grab hover:-translate-y-0.5 hover:border-line-strong hover:shadow-pop'
            : 'hover:border-line-strong'
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-y-2.5 left-0 w-[3px] rounded-full',
          PRIORITY_ACCENT[request.priority] ?? 'bg-line-strong'
        )}
      />

      <div className="mb-1.5 flex items-center justify-between gap-2">
        <Badge tone={PRIORITY_TONE[request.priority] ?? 'slate'} className="px-1.5 py-0">
          {PRIORITY_LABEL[request.priority] ?? request.priority}
        </Badge>
        <div className="flex items-center gap-1">
          <span className="text-2xs tabular-nums text-ink-faint">
            {new Date(request.created_at).toLocaleDateString('zh-CN', {
              month: '2-digit',
              day: '2-digit',
            })}
          </span>
          {draggable && (
            <Icon
              name="grip"
              className="h-3.5 w-3.5 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100"
            />
          )}
        </div>
      </div>

      <h4 className="text-[13px] font-semibold leading-snug text-ink">{request.title}</h4>
      {request.description && (
        <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-ink-muted">
          {request.description}
        </p>
      )}

      <div className="mt-2.5 flex items-center gap-2 border-t border-line-subtle pt-2">
        {request.attachments.length > 0 && (
          <span
            className="inline-flex items-center gap-0.5 text-2xs text-ink-faint"
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
            className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 text-2xs font-medium text-accent transition-colors hover:bg-brand-100"
          >
            <Icon name="play" className="h-2.5 w-2.5" />
            查看运行
          </Link>
        ) : (
          <span className="text-2xs text-ink-faint">{draggable ? '拖拽分配' : ''}</span>
        )}

        {onOpen && (
          <button
            type="button"
            onPointerDown={stopDrag}
            onClick={(e) => {
              e.stopPropagation()
              onOpen()
            }}
            className="ml-auto rounded-md px-1.5 py-0.5 text-2xs font-medium text-ink-faint transition-colors hover:bg-surface-subtle hover:text-ink-soft"
          >
            详情
          </button>
        )}
      </div>
    </div>
  )
}
