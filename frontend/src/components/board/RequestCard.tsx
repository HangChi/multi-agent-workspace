/** 需求卡片（T047 + T056 + 008）：看板上的最小单元，可拖拽分配/分派。
 *
 * 设计取舍（对齐 Linear / Trello 的卡片密度）：
 * - 只放「标题 + 一行元信息」。描述不进卡片：它让每张卡高低不齐、
 *   一屏只能看五六张，而描述在详情弹层里一字不少；
 * - 优先级用左侧 3px 色条表达，不再占一行彩色标签；
 * - 元信息行 12px 灰色：附件数、创建日期、运行入口；
 * - 「详情」入口两条路：整卡点击（PointerSensor 6px 激活约束区分点击与
 *   拖拽）+ 显式按钮。按钮在触屏常驻可见（没有 hover 的设备不能把
 *   唯一入口藏在 hover 里），桌面仍只在 hover/focus 时出现。
 */

import { useDraggable } from '@dnd-kit/core'
import { memo, useCallback } from 'react'
import { Link } from 'react-router-dom'

import { WorkRequest } from '../../api/endpoints'
import { useBoardStore } from '../../stores/boardStore'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

/** 卡片左侧的优先级色条：高/中优先级用微渐变，低优先级退化为中性灰。
 *  纯色三条线在图里没有「热度」差异，渐变让优先级真的可扫读。 */
const PRIORITY_ACCENT: Record<string, string> = {
  high: 'bg-gradient-to-b from-rose-400 to-rose-500',
  normal: 'bg-gradient-to-b from-brand-400 to-brand-500',
  low: 'bg-line-strong',
}

interface Props {
  request: WorkRequest
  /** 是否可拖拽（created/pending/accepted/assigned 可转交或分配）。 */
  draggable?: boolean
  /**
   * 打开详情弹层。
   *
   * 收 `request` 而不是闭包：父组件因此能传**同一个**函数引用（见
   * `Board` 里的 `handleOpen`），配 `memo` 才有意义。此前是每张卡一个
   * 新箭头函数，memo 完全失效（P7）。
   */
  onOpen?: (request: WorkRequest) => void
}

function RequestCardImpl({ request, draggable = true, onOpen }: Props) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `request:${request.id}`,
    data: { kind: 'request', id: request.id, title: request.title },
    disabled: !draggable,
  })

  const runId = useBoardStore((s) => s.runByRequest[request.id])

  const handleOpen = useCallback(() => {
    onOpen?.(request)
  }, [onOpen, request])

  // 拖拽时原卡片退化为「占位残影」，真正的预览由 Board 的 DragOverlay 渲染，
  // 避免被列容器裁切、也不会和侧栏重叠。
  const style = { opacity: isDragging ? 0.35 : 1 }

  /** 卡片内的按钮/链接不参与拖拽，避免「想点却拖起来」。 */
  const stopDrag = (e: React.PointerEvent) => e.stopPropagation()
  /** 卡片内链接/按钮的点击不冒泡到整卡（否则会同时打开详情）。 */
  const stopClick = (e: React.MouseEvent) => e.stopPropagation()

  return (
    <article
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      // 整卡点击开详情：拖拽有 6px 激活约束兜底，不会误触。
      // 卡片内的链接/按钮各自 stopClick，导航与开详情不打架。
      onClick={onOpen && !isDragging ? handleOpen : undefined}
      className={cn(
        // 白卡 + 柔和双层阴影 + 半透明边框：从灰底列里「浮」出来。
        // 此前 border + 无阴影，卡片像贴在列上的一张纸
        'group relative overflow-hidden rounded-lg border border-line/70 bg-surface shadow-card transition-[box-shadow,border-color,transform,opacity] duration-200 ease-out-expo',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-1',
        isDragging
          ? 'cursor-grabbing border-dashed border-brand-300 bg-brand-50/40 opacity-60 shadow-none'
          : draggable
            ? 'cursor-grab hover:-translate-y-0.5 hover:border-line-strong hover:shadow-lift'
            : 'hover:border-line-strong hover:shadow-lift'
      )}
    >
      {/* 优先级色条：见 PRIORITY_ACCENT（高/中用微渐变，低优先级退化为灰） */}
      <span
        aria-hidden="true"
        className={cn(
          'absolute inset-y-0 left-0 w-[3px]',
          PRIORITY_ACCENT[request.priority] ?? 'bg-line-strong'
        )}
      />

      <div className="px-3 py-2.5 pl-3.5">
        <h4 className="line-clamp-2 text-body font-medium leading-snug tracking-tight text-ink">
          {request.title}
        </h4>

        {/* 008：项目徽章。与「定时」徽标同款密度（一行 micro 文字），
            无归属不渲染——「未归类」不是错误状态，不该占视觉空间。
            点击直接按该项目筛选（与看板下拉同源）。 */}
        {request.project_name && (
          <Link
            to={`/?project=${request.project_id}`}
            onPointerDown={stopDrag}
            onClick={stopClick}
            className="mt-1 inline-flex max-w-full items-center gap-1 rounded-md bg-surface-muted px-1.5 py-0.5 text-micro text-ink-muted transition-colors hover:bg-brand-50 hover:text-accent dark:hover:bg-brand-500/10"
            title={`按项目「${request.project_name}」筛选`}
          >
            <Icon name="layers" className="h-2.5 w-2.5 shrink-0" />
            <span className="truncate">{request.project_name}</span>
          </Link>
        )}

        {/* 009：未完成前置徽章。门禁的原因要前置可见——被挡下的需求拖不动，
            卡片上直接说清，否则用户把 409 当成故障（US4）。hover 逐条标题。 */}
        {(request.unmet_count ?? 0) > 0 && (
          <span
            className="mt-1 inline-flex items-center gap-1 rounded-md bg-amber-50 px-1.5 py-0.5 text-micro text-amber-700 ring-1 ring-inset ring-amber-200/70 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/25"
            title={`前置未完成：${(request.unmet_titles ?? []).join('、')}——打开详情查看`}
          >
            <Icon name="clock" className="h-2.5 w-2.5 shrink-0" />
            {request.unmet_count} 个前置未完成
          </span>
        )}

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
                onClick={stopClick}
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
                  handleOpen()
                }}
                className="ml-auto rounded px-1.5 py-0.5 text-micro text-ink-muted transition-all hover:bg-surface-subtle hover:text-ink focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 group-hover:opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
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

/**
 * 卡片在看板上是最高频的重渲染对象（拖拽期间每帧都在 render）。
 *
 * 只在 `request` / `draggable` / `onOpen` 三个入参真正变化时重渲染：
 * 拖拽时 `useDraggable` 的 `isDragging` 变化由 dnd-kit 内部订阅驱动，
 * 不经过父组件的 props，所以不影响这里的比较。
 */
export const RequestCard = memo(RequestCardImpl)
