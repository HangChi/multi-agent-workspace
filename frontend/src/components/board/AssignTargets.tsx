/** 分配目标条（T056）：Agent / 组作为拖放目标，拖拽悬停时整块高亮。
 *
 * 从右侧 292px 的固定栏改成看板上方的横向条：
 * 一列太窄会把卡片标题挤成三行，而看板的价值恰恰在「一眼扫完」。
 * 改成横条后，列宽能稳定在 280px 左右，所有状态同屏可比。
 * 每个目标是一枚可放置的胶囊（40px 高），悬停即高亮并给出松手提示。
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
      title={`拖到这里分配给「${name}」`}
      className={cn(
        'flex h-10 shrink-0 items-center gap-2 rounded-lg border px-2.5 transition-all duration-150',
        isOver
          ? 'border-brand-400 bg-brand-50 shadow-sm ring-2 ring-brand-200 dark:bg-brand-500/10 dark:ring-brand-500/30'
          : 'border-line bg-surface hover:border-line-strong hover:bg-surface-muted'
      )}
    >
      {isGroup ? (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/25">
          <Icon name="users" className="h-3.5 w-3.5" />
        </span>
      ) : (
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-sky-500 to-brand-600 text-[10px] font-semibold text-white">
          {initials}
        </span>
      )}
      <span className="min-w-0">
        <span className="block max-w-[9rem] truncate text-[12.5px] font-medium leading-tight text-ink">
          {name}
        </span>
        <span className="block max-w-[9rem] truncate text-[10px] leading-tight text-ink-faint">
          {meta}
        </span>
      </span>
      {isOver && <Icon name="arrow-down" className="h-3.5 w-3.5 shrink-0 text-brand-500" />}
    </div>
  )
}

interface Props {
  agents: Agent[]
  groups: Group[]
}

export function AssignTargets({ agents, groups }: Props) {
  return (
    <section
      aria-label="分配目标"
      className="rounded-xl border border-line/80 bg-surface/80 px-2.5 py-2 shadow-card backdrop-blur-sm"
    >
      <div className="flex items-center gap-3">
        <div className="flex shrink-0 items-center gap-1.5 border-r border-line pr-3">
          <Icon name="target" className="h-3.5 w-3.5 text-brand-500" />
          <span className="text-2xs font-semibold uppercase tracking-[0.12em] text-ink-faint">
            拖放目标
          </span>
          <span className="rounded-full bg-surface-subtle px-1.5 text-2xs font-medium tabular-nums text-ink-muted">
            {agents.length + groups.length}
          </span>
        </div>

        <div className="scrollbar-none flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto py-0.5">
          {agents.map((a) => (
            <TargetChip
              key={a.id}
              id={a.id}
              kind="agent"
              name={a.name}
              meta={`${a.model} · ${a.tools.length} 工具`}
              initials={a.name.slice(0, 1)}
            />
          ))}

          {groups.length > 0 && (
            <span className="mx-0.5 h-6 w-px shrink-0 bg-line" aria-hidden="true" />
          )}

          {groups.map((g) => (
            <TargetChip
              key={g.id}
              id={g.id}
              kind="group"
              name={g.name}
              meta={`${STRATEGY_LABELS[g.routing_strategy]} · ${g.members.length} 成员`}
              isGroup
            />
          ))}

          {agents.length === 0 && groups.length === 0 && (
            <p className="py-2 text-2xs text-ink-faint">
              还没有可分配的 Agent 或组，先到「Agent 管理」创建一个。
            </p>
          )}
        </div>
      </div>
    </section>
  )
}
