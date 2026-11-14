/** 分配确认弹窗（T057）：拖到 Agent 直接确认 / 拖到组选策略（默认组配置）。 */

import { useState } from 'react'

import {
  Agent,
  Group,
  RoutingStrategy,
  WorkRequest,
} from '../../api/endpoints'

const STRATEGY_OPTIONS: { value: RoutingStrategy; label: string; desc: string }[] = [
  { value: 'direct', label: '直接路由', desc: '请求直接交给该 Agent' },
  { value: 'fan_out', label: '广播', desc: '同时发给所有成员，汇总结果' },
  { value: 'pipeline', label: '流水线', desc: '按顺序串联执行（后续 Phase 支持）' },
  { value: 'router', label: '路由器', desc: '按内容智能分发（后续 Phase 支持）' },
]

const STRATEGY_LABELS: Record<RoutingStrategy, string> = {
  direct: '直接路由',
  fan_out: '广播',
  pipeline: '流水线',
  router: '路由器',
}

export type AssignTarget =
  | { kind: 'agent'; id: string }
  | { kind: 'group'; id: string }

interface Props {
  request: WorkRequest
  target: AssignTarget
  /** 拖到的目标详情（Agent 或组）。 */
  agent?: Agent
  group?: Group
  submitting: boolean
  error: string | null
  onConfirm: (payload: { strategy: RoutingStrategy }) => void
  onCancel: () => void
}

export function AssignModal({
  request,
  target,
  agent,
  group,
  submitting,
  error,
  onConfirm,
  onCancel,
}: Props) {
  // 拖到组时默认取组配置的路由策略
  const [strategy, setStrategy] = useState<RoutingStrategy>(
    group?.routing_strategy ?? 'direct',
  )

  const isAgent = target.kind === 'agent'
  const targetName = isAgent ? agent?.name ?? '该 Agent' : group?.name ?? '该组'
  const targetBadge = isAgent
    ? '直接分配'
    : `策略：${STRATEGY_LABELS[strategy]}`

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-semibold text-slate-800">确认分配</h2>
        <p className="mt-1 text-sm text-slate-500">
          将需求「{request.title}」分配给
          <span className="mx-1 font-medium text-slate-700">{targetName}</span>
        </p>

        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-blue-50 px-2 py-0.5 font-medium text-blue-700">
            优先级 {request.priority}
          </span>
          <span
            className={`rounded-full px-2 py-0.5 font-medium ${
              isAgent
                ? 'bg-emerald-50 text-emerald-700'
                : 'bg-violet-50 text-violet-700'
            }`}
          >
            {targetBadge}
          </span>
        </div>

        {!isAgent && group && (
          <div className="mt-4">
            <p className="mb-2 text-sm font-medium text-slate-700">路由策略</p>
            <div className="space-y-2">
              {STRATEGY_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                    strategy === opt.value
                      ? 'border-blue-500 bg-blue-50'
                      : 'border-slate-200 hover:bg-slate-50'
                  }`}
                >
                  <input
                    type="radio"
                    name="strategy"
                    value={opt.value}
                    checked={strategy === opt.value}
                    onChange={() => setStrategy(opt.value)}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="block text-sm font-medium text-slate-800">
                      {opt.label}
                    </span>
                    <span className="block text-xs text-slate-500">{opt.desc}</span>
                  </span>
                </label>
              ))}
            </div>
            {group.members.length === 0 && (
              <p className="mt-2 text-xs text-amber-600">
                该组暂无成员，广播策略将无法执行
              </p>
            )}
          </div>
        )}

        {error && (
          <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">
            {error}
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
          >
            取消
          </button>
          <button
            onClick={() => onConfirm({ strategy })}
            disabled={submitting}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {submitting ? '分配中...' : '确认分配'}
          </button>
        </div>
      </div>
    </div>
  )
}
