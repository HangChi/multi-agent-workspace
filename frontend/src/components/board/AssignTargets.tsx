/** 分配目标栏（T056）：Agent/组作为拖放目标（useDroppable），拖拽中高亮。 */

import { useDroppable } from '@dnd-kit/core'

import { Agent, Group, RoutingStrategy } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

const STRATEGY_LABELS: Record<RoutingStrategy, string> = {
  direct: '直接',
  fan_out: '广播',
  pipeline: '流水线',
  router: '路由器',
}

/** 可放置的 Agent 卡。 */
function AgentDropCard({ agent }: { agent: Agent }) {
  const { setNodeRef, isOver } = useDroppable({
    id: `agent:${agent.id}`,
    data: { kind: 'agent', id: agent.id },
  })
  return (
    <div
      ref={setNodeRef}
      className={cn(
        'rounded-lg border p-2.5 transition-all duration-150',
        isOver
          ? 'border-brand-400 bg-brand-50 shadow-md ring-2 ring-brand-200'
          : 'border-line bg-surface hover:border-line-strong hover:bg-surface-muted/70',
      )}
    >
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-sky-500 to-brand-600 text-2xs font-semibold text-white">
          {agent.name.slice(0, 1)}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium text-ink">{agent.name}</p>
          <p className="truncate text-2xs text-ink-faint">
            {agent.model} · {agent.tools.length} 个工具
          </p>
        </div>
        <Icon
          name={isOver ? 'arrow-down' : 'target'}
          className={cn('h-3.5 w-3.5 shrink-0', isOver ? 'text-brand-500' : 'text-ink-faint')}
        />
      </div>
      {isOver && (
        <div className="mt-1.5 rounded-md bg-brand-600 py-1 text-center text-2xs font-medium text-white animate-fade-in">
          松开即分配给此 Agent
        </div>
      )}
    </div>
  )
}

/** 可放置的组卡。 */
function GroupDropCard({ group }: { group: Group }) {
  const { setNodeRef, isOver } = useDroppable({
    id: `group:${group.id}`,
    data: { kind: 'group', id: group.id },
  })
  return (
    <div
      ref={setNodeRef}
      className={cn(
        'rounded-lg border p-2.5 transition-all duration-150',
        isOver
          ? 'border-sky-400 bg-sky-50 shadow-md ring-2 ring-sky-200'
          : 'border-line bg-surface hover:border-line-strong hover:bg-surface-muted/70',
      )}
    >
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100">
          <Icon name="users" className="h-3.5 w-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium text-ink">{group.name}</p>
          <p className="truncate text-2xs text-ink-faint">
            {STRATEGY_LABELS[group.routing_strategy]} · {group.members.length} 个成员
          </p>
        </div>
        <Icon
          name={isOver ? 'arrow-down' : 'target'}
          className={cn('h-3.5 w-3.5 shrink-0', isOver ? 'text-sky-500' : 'text-ink-faint')}
        />
      </div>
      {isOver && (
        <div className="mt-1.5 rounded-md bg-sky-600 py-1 text-center text-2xs font-medium text-white animate-fade-in">
          松开后选择路由策略
        </div>
      )}
    </div>
  )
}

interface Props {
  agents: Agent[]
  groups: Group[]
}

export function AssignTargets({ agents, groups }: Props) {
  return (
    <aside className="w-full shrink-0 xl:sticky xl:top-[72px] xl:w-[292px]">
      <div className="rounded-xl border border-line/90 bg-surface shadow-card">
        <div className="flex items-start gap-2.5 border-b border-line-subtle px-3.5 py-3">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-50 text-accent ring-1 ring-inset ring-brand-100">
            <Icon name="target" className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <p className="text-[13px] font-semibold text-ink">分配目标</p>
            <p className="mt-0.5 text-2xs leading-relaxed text-ink-faint">
              拖到这里 → Agent 直接分配 / 组选择策略
            </p>
          </div>
        </div>

        <div className="max-h-[calc(100vh-260px)] space-y-3 overflow-y-auto p-3 xl:max-h-[calc(100vh-300px)]">
          <div>
            <div className="mb-1.5 flex items-center justify-between px-0.5">
              <span className="text-2xs font-semibold uppercase tracking-wide text-ink-faint">
                Agent
              </span>
              <Badge tone="emerald" dot>
                {agents.length} 活跃
              </Badge>
            </div>
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 xl:grid-cols-1">
              {agents.map((a) => (
                <AgentDropCard key={a.id} agent={a} />
              ))}
              {agents.length === 0 && (
                <p className="rounded-lg border border-dashed border-line py-3 text-center text-2xs text-ink-faint">
                  暂无活跃 Agent
                </p>
              )}
            </div>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between px-0.5">
              <span className="text-2xs font-semibold uppercase tracking-wide text-ink-faint">
                组
              </span>
              <Badge tone="sky">{groups.length} 个</Badge>
            </div>
            <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 xl:grid-cols-1">
              {groups.map((g) => (
                <GroupDropCard key={g.id} group={g} />
              ))}
              {groups.length === 0 && (
                <p className="rounded-lg border border-dashed border-line py-3 text-center text-2xs text-ink-faint">
                  暂无 Agent 组
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </aside>
  )
}
