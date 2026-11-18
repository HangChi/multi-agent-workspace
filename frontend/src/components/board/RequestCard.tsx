/** 需求卡片（T047 + T056）：看板上的最小单元，可拖拽分配给 Agent/组。
 *
 * 设计取舍（对齐 Linear / Trello 的卡片密度）：
 * - 只放「标题 + 一行元信息」。描述不进卡片：它让每张卡高低不齐、
 *   一屏只能看五六张，而描述在详情弹层里一字不少；
 * - 优先级用左侧 3px 色条表达，不再占一行彩色标签；
 * - 元信息行 12px 灰色：附件数、创建日期、运行入口；
 *   「详情」按钮只在 hover / focus 时出现，不占常驻视觉空间。
 */

import { useDraggable } from '@dnd-kit/core'
import { Link } from 'react-router-dom'

import { WorkRequest } from '../../api/endpoints'
import { useBoardStore } from '../../stores/boardStore'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

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
    <article
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      className={cn(
        'group relative overflow-hidden rounded-lg border border-line/80 bg-surface transition-[border-color,box-shadow,opacity] duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-1',
        isDragging
          ? 'cursor-grabbing border-dashed border-brand-300 bg-brand-50/40 opacity-60 shadow-none'
          : draggable
            ? 'cursor-grab hover:border-line-strong hover:shadow-panel'
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
        <h4 className="line-clamp-2 text-body font-medium leading-snug text-ink">
          {request.title}
        </h4>

        <div className="mt-2 flex items-center gap-2">
          {/* 来源徽标放在卡片上而不是只在详情里：看板上要能一眼分辨
              「人建的」和「规则建的」，否则一堆自动需求看起来像是谁手动拖进来的 */}
          {request.source_schedule_id && (
            <span
              className="inline-flex items-center gap-0.5 text-micro text-ink-faint"
              title={
                request.source_schedule_name
                  ? `来自调度「${request.source_schedule_name}」`
                  : '由调度自动创建'
              }
            >
              <Icon name="clock" className="h-3 w-3" />
              定时
            </span>
          )}

          {request.attachments.length > 0 && (
            <span
              className="inline-flex items-center gap-0.5 text-micro text-ink-faint"
              title={`${request.attachments.length} 个附件`}
            >
              <Icon name="paperclip" className="h-3 w-3" />
              {request.attachments.length}
            </span>
          )}

          {/* 运行数：区分「刚建的需求」和「已经跑过几轮」*/}
          {(request.runs_summary?.total ?? 0) > 0 && (
            <span
              className="inline-flex items-center gap-0.5 text-micro text-ink-faint"
              title={`${request.runs_summary?.completed ?? 0} 完成 / ${
                request.runs_summary?.failed ?? 0
              } 失败`}
            >
              {request.runs_summary?.total} 个运行
            </span>
          )}

          <span className="ml-auto text-micro tabular-nums text-ink-faint">
            {new Date(request.created_at).toLocaleDateString('zh-CN', {
              month: '2-digit',
              day: '2-digit',
            })}
          </span>
        </div>

        {/* 操作行：常驻只保留「查看运行」（有运行时才有意义），
            其余按需出现，避免每张卡都挂一排按钮 */}
        {(runId || onOpen) && (
          <div className="mt-1.5 flex items-center gap-1.5">
            {runId ? (
              <Link
                to={`/runs/${runId}`}
                onPointerDown={stopDrag}
                className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-micro font-medium text-accent transition-colors hover:bg-brand-50 dark:hover:bg-brand-500/10"
              >
                <Icon name="play" className="h-2.5 w-2.5" />
                查看运行
              </Link>
            ) : (
              <span className="text-micro text-ink-faint opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                拖拽分配
              </span>
            )}
            {onOpen && (
              <button
                type="button"
                onPointerDown={stopDrag}
                onClick={(e) => {
                  e.stopPropagation()
                  onOpen()
                }}
                className="ml-auto rounded px-1.5 py-0.5 text-micro text-ink-muted opacity-0 transition-all hover:bg-surface-subtle hover:text-ink focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 group-hover:opacity-100"
              >
                详情
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
