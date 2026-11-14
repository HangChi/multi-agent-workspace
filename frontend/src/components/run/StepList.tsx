/** 步骤列表（T072，US5）：按 seq 展示 LLM/工具/最终步骤，纵向时间线。 */

import type { RunStep } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Icon } from '../ui/Icons'
import type { IconName } from '../ui/Icons'
import { cn } from '../ui/cn'

const KIND_META: Record<RunStep['kind'], { label: string; icon: IconName }> = {
  llm: { label: 'LLM 调用', icon: 'sparkles' },
  tool: { label: '工具调用', icon: 'zap' },
  final: { label: '最终结果', icon: 'check-circle' },
}

const STATUS_META: Record<RunStep['status'], { label: string; tone: BadgeTone }> = {
  running: { label: '执行中', tone: 'amber' },
  completed: { label: '完成', tone: 'emerald' },
  failed: { label: '失败', tone: 'rose' },
}

function formatDuration(ms: number): string {
  if (ms <= 0) return '—'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

export function StepList({ steps }: { steps: RunStep[] }) {
  if (steps.length === 0) {
    return (
      <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed border-line py-8 text-center">
        <Icon name="clock" className="h-5 w-5 text-ink-faint" />
        <p className="text-sm text-ink-faint">暂无执行步骤</p>
        <p className="text-xs text-ink-faint">运行开始后，每一步都会实时出现在这里</p>
      </div>
    )
  }

  return (
    <ol className="relative">
      {steps.map((step, i) => {
        const kind = KIND_META[step.kind]
        const status = STATUS_META[step.status]
        const isLast = i === steps.length - 1
        return (
          <li key={step.id} className="relative flex gap-3 pb-3 last:pb-0">
            {/* 连接线 */}
            {!isLast && (
              <span
                aria-hidden="true"
                className="absolute left-[13px] top-8 h-[calc(100%-28px)] w-px bg-line"
              />
            )}

            <span
              className={cn(
                'relative z-10 mt-1 flex h-[27px] w-[27px] shrink-0 items-center justify-center rounded-full ring-4 ring-white',
                step.status === 'completed'
                  ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-300'
                  : step.status === 'failed'
                    ? 'bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-300'
                    : 'bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-300',
              )}
            >
              {step.status === 'running' ? (
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-amber-500" />
                </span>
              ) : (
                <Icon name={step.status === 'failed' ? 'x' : 'check'} className="h-3.5 w-3.5" />
              )}
            </span>

            <div className="min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 py-2.5 shadow-card transition-colors hover:border-line-strong">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-2xs font-medium tabular-nums text-ink-faint">
                  #{step.seq}
                </span>
                <span className="inline-flex items-center gap-1 text-[13px] font-medium text-ink">
                  <Icon name={kind.icon} className="h-3.5 w-3.5 text-ink-faint" />
                  {kind.label}
                </span>
                {step.tool_name && (
                  <span className="rounded bg-surface-subtle px-1.5 py-0.5 font-mono text-2xs text-ink-soft">
                    {step.tool_name}
                  </span>
                )}
                <span className="ml-auto flex items-center gap-2">
                  <Badge tone={status.tone} dot pulse={step.status === 'running'}>
                    {status.label}
                  </Badge>
                </span>
              </div>

              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-ink-faint">
                <span className="inline-flex items-center gap-1">
                  <Icon name="clock" className="h-3 w-3" />
                  {formatDuration(step.duration_ms)}
                </span>
                <span className="inline-flex items-center gap-1 tabular-nums">
                  <Icon name="coins" className="h-3 w-3" />
                  ¥{(step.cost_cents / 100).toFixed(2)}
                </span>
                <span className="ml-auto tabular-nums">
                  {new Date(step.created_at).toLocaleTimeString('zh-CN')}
                </span>
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
