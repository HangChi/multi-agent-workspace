/** 分配目标栏（T056）：Agent/组作为拖放目标（useDroppable），拖拽中高亮。 */

import { useDroppable } from '@dnd-kit/core'

import { Agent, Group, RoutingStrategy } from '../../api/endpoints'

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
      className={`rounded-xl border p-3 transition-all ${
        isOver
          ? 'border-blue-500 bg-blue-50 shadow-md ring-2 ring-blue-200'
          : 'border-slate-200 bg-white shadow-sm'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <h4 className="truncate text-sm font-semibold text-slate-800">{agent.name}</h4>
        <span className="shrink-0 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
          活跃
        </span>
      </div>
      {agent.description && (
        <p className="mt-1 line-clamp-1 text-xs text-slate-500">{agent.description}</p>
      )}
      <div className="mt-1.5 flex items-center gap-2 text-xs text-slate-400">
        <span>{agent.model}</span>
        <span>{agent.tools.length} 工具</span>
      </div>
      {isOver && (
        <div className="mt-2 rounded bg-blue-600 px-2 py-1 text-center text-xs font-medium text-white">
          松开分配给此 Agent
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
      className={`rounded-xl border p-3 transition-all ${
        isOver
          ? 'border-violet-500 bg-violet-50 shadow-md ring-2 ring-violet-200'
          : 'border-slate-200 bg-white shadow-sm'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <h4 className="truncate text-sm font-semibold text-slate-800">{group.name}</h4>
        <span className="shrink-0 rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700">
          {STRATEGY_LABELS[group.routing_strategy]}
        </span>
      </div>
      {group.description && (
        <p className="mt-1 line-clamp-1 text-xs text-slate-500">{group.description}</p>
      )}
      <div className="mt-1.5 text-xs text-slate-400">{group.members.length} 个成员</div>
      {isOver && (
        <div className="mt-2 rounded bg-violet-600 px-2 py-1 text-center text-xs font-medium text-white">
          松开选择策略分配
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
    <aside className="w-72 shrink-0 space-y-4">
      <div className="rounded-xl bg-slate-50 p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-semibold text-slate-700">拖放目标</span>
          <span className="text-xs text-slate-400">把需求卡拖到这里</span>
        </div>
        <p className="mb-3 text-xs leading-relaxed text-slate-400">
          从「待分配」列拖拽需求卡片到 Agent（直接分配）或组（选择策略）上。
        </p>
      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Agent（{agents.length}）
        </h3>
        <div className="space-y-2">
          {agents.map((a) => (
            <AgentDropCard key={a.id} agent={a} />
          ))}
          {agents.length === 0 && (
            <div className="rounded-lg border border-dashed border-slate-200 py-4 text-center text-xs text-slate-400">
              暂无活跃 Agent
            </div>
          )}
        </div>
      </div>

      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          组（{groups.length}）
        </h3>
        <div className="space-y-2">
          {groups.map((g) => (
            <GroupDropCard key={g.id} group={g} />
          ))}
          {groups.length === 0 && (
            <div className="rounded-lg border border-dashed border-slate-200 py-4 text-center text-xs text-slate-400">
              暂无组
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}
