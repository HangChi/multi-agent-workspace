/** Agent 列表项（T037）。 */

import { Agent } from '../../api/endpoints'

interface Props {
  agent: Agent
  onEdit: () => void
  onArchive: () => void
  onDelete: () => void
}

export function AgentListItem({ agent, onEdit, onArchive, onDelete }: Props) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate font-semibold text-slate-800">{agent.name}</h3>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                agent.status === 'active'
                  ? 'bg-emerald-50 text-emerald-700'
                  : 'bg-slate-100 text-slate-500'
              }`}
            >
              {agent.status === 'active' ? '活跃' : '已归档'}
            </span>
          </div>
          {agent.description && (
            <p className="mt-1 line-clamp-2 text-sm text-slate-500">
              {agent.description}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
            <span>{agent.model}</span>
            <span>{agent.tools.join(' / ')}</span>
            <span>上限 ¥{(agent.cost_limit_cents / 100).toFixed(2)}</span>
            <span>
              {new Date(agent.created_at).toLocaleDateString('zh-CN')}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={onEdit}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
          >
            编辑
          </button>
          {agent.status === 'active' ? (
            <button
              onClick={onArchive}
              className="rounded-lg border border-amber-300 px-3 py-1.5 text-xs font-medium text-amber-600 hover:bg-amber-50"
            >
              归档
            </button>
          ) : null}
          <button
            onClick={onDelete}
            className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"
          >
            删除
          </button>
        </div>
      </div>
    </div>
  )
}
