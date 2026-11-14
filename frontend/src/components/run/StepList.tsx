/** 步骤列表（T072，US5）：按 seq 展示 LLM/工具/最终步骤。 */

import type { RunStep } from '../../api/endpoints'

const KIND_LABEL: Record<RunStep['kind'], string> = {
  llm: 'LLM 调用',
  tool: '工具调用',
  final: '最终结果',
}

const STATUS_STYLE: Record<RunStep['status'], string> = {
  running: 'bg-amber-100 text-amber-700',
  completed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
}

export function StepList({ steps }: { steps: RunStep[] }) {
  if (steps.length === 0) {
    return <p className="text-sm text-slate-400">暂无执行步骤</p>
  }

  return (
    <ol className="space-y-2">
      {steps.map((step) => (
        <li
          key={step.id}
          className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm"
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-slate-100 text-xs font-medium text-slate-600">
                {step.seq}
              </span>
              <span className="text-sm font-medium text-slate-800">
                {KIND_LABEL[step.kind]}
              </span>
              {step.tool_name && (
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
                  {step.tool_name}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span
                className={`rounded px-1.5 py-0.5 text-xs font-medium ${STATUS_STYLE[step.status]}`}
              >
                {step.status}
              </span>
              {step.cost_cents > 0 && (
                <span className="text-xs text-slate-400">
                  ¥{(step.cost_cents / 100).toFixed(2)}
                </span>
              )}
            </div>
          </div>
          {step.duration_ms > 0 && (
            <p className="mt-1 text-xs text-slate-400">
              耗时 {(step.duration_ms / 1000).toFixed(1)}s
            </p>
          )}
        </li>
      ))}
    </ol>
  )
}
