/** 分配目标条（T056）：Agent / 组作为拖放目标，拖拽悬停时整块高亮。
 *
 * 版式取舍：
 * - 目标做成 32px 的「头像 + 名字」胶囊，一行排开，可横向滚动。
 *   早期的版本在胶囊里塞了模型与工具数，结果一排胶囊宽窄不一、
 *   还把「这里能拖」这个信息淹没了；
 * - 现在只在 hover 时用 title 提示「拖到这里分配给 X」，
 *   悬停态（描边 + 浅蓝底 + 箭头）已经把可放置性说清楚了；
 * - 左侧一个固定的小标签 + 计数，让人一眼知道这排东西是干什么的。
 */

import { useDroppable } from '@dnd-kit/core'

import { Agent, Group, RoutingStrategy } from '../../api/endpoints'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

const STRATEGY_LABELS: Record<RoutingStrategy, string> = {
  direct: '直接',
  fan_out: '广播',
  pipeline: '流水线',
  router: '路由器',
}

function TargetChip({
  id,
  kind,
  name,
  meta,
  isGroup,
  initials,
}: {
  id: string
  kind: 'agent' | 'group'
  name: string
  meta: string
  isGroup?: boolean
  initials?: string
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `${kind}:${id}`, data: { kind, id } })

  return (
    <div
      ref={setNodeRef}
      title={`拖到这里分配给「${name}」 · ${meta}`}
      className={cn(
        'flex h-8 shrink-0 items-center gap-1.5 rounded-md border pl-1 pr-2 transition-colors duration-150',
        isOver
          ? 'border-brand-400 bg-brand-50 shadow-sm dark:bg-brand-500/10'
          : 'border-line bg-surface hover:border-line-strong hover:bg-surface-muted'
      )}
    >
      {isGroup ? (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/25">
          <Icon name="users" className="h-3.5 w-3.5" />
        </span>
      ) : (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-gradient-to-br from-sky-500 to-brand-600 text-micro font-semibold text-white">
          {initials}
        </span>
      )}
      <span className="max-w-[9rem] truncate text-ui font-medium text-ink">{name}</span>
      {isOver && <Icon name="arrow-down" className="h-3.5 w-3.5 shrink-0 text-brand-500" />}
    </div>
  )
}

interface Props {
  agents: Agent[]
  groups: Group[]
}

export function AssignTargets({ agents, groups }: Props) {
  if (agents.length === 0 && groups.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-line px-3 py-2 text-ui text-ink-faint">
        还没有可分配的 Agent 或组 —— 到「Agent 管理」创建一个，再回来把需求拖给它。
      </p>
    )
  }

  return (
    <section aria-label="分配目标" className="flex items-center gap-2.5">
      <span className="flex shrink-0 items-center gap-1.5 text-micro font-medium text-ink-faint">
        <Icon name="target" className="h-3.5 w-3.5" />
        拖到目标
        <span className="tabular-nums">({agents.length + groups.length})</span>
      </span>

      <div className="scrollbar-none flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto py-0.5">
        {agents.map((a) => (
          <TargetChip
            key={a.id}
            id={a.id}
            kind="agent"
            name={a.name}
            meta={`${a.model} · ${a.tools.length} 个工具`}
            initials={a.name.slice(0, 1)}
          />
        ))}

        {groups.length > 0 && agents.length > 0 && (
          <span className="mx-0.5 h-5 w-px shrink-0 bg-line" aria-hidden="true" />
        )}

        {groups.map((g) => (
          <TargetChip
            key={g.id}
            id={g.id}
            kind="group"
            name={g.name}
            meta={`${STRATEGY_LABELS[g.routing_strategy]} · ${g.members.length} 个成员`}
            isGroup
          />
        ))}
      </div>
    </section>
  )
}
