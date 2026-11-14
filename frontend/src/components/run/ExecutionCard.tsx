import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'

export interface ExecutionSummary {
  id: string
  step_seq: number
  language: string
  status: string
  exit_code: number | null
  duration_ms: number
  stdout_truncated: boolean
  artifacts: number
}

export function ExecutionCard({ execution }: { execution: ExecutionSummary }) {
  const failed = execution.status !== 'ok'
  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-ink">沙箱执行 · {execution.language}</span>
          <Badge tone={failed ? 'rose' : 'emerald'}>{execution.status}</Badge>
        </div>
        <span className="text-xs text-ink-faint">第 {execution.step_seq} 步</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs text-ink-muted sm:grid-cols-4">
        <span>退出码：{execution.exit_code ?? '—'}</span>
        <span>耗时：{execution.duration_ms} ms</span>
        <span>制品：{execution.artifacts}</span>
        {execution.stdout_truncated && <span className="text-amber-600">输出已截断</span>}
      </div>
    </Card>
  )
}
