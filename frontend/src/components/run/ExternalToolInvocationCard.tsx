/** Safe timeline card for one external MCP invocation. */

import { Badge } from '../ui/Badge'
import { Card } from '../ui/Card'

export interface ExternalToolInvocation {
  id: string
  step_id: string | null
  server_name: string
  tool_name: string
  stable_name: string
  version: string
  status: string
  duration_ms: number
  error_category: string | null
  result_truncated: boolean
  created_at: string
}

const STATUS_LABEL: Record<string, string> = {
  succeeded: '成功',
  parameter_error: '参数错误',
  policy_denied: '策略拒绝',
  transport_error: '连接失败',
  auth_error: '认证失败',
  timeout: '超时',
  tool_error: '工具错误',
  result_truncated: '结果已截断',
  cancelled: '已取消',
}

export function ExternalToolInvocationCard({ invocation }: { invocation: ExternalToolInvocation }) {
  const failed = invocation.status !== 'succeeded'
  // running 时后端还没有 duration，显示「0 ms」是误导——改成「进行中」
  const running = invocation.status === 'running'
  return (
    <Card className="border-brand-200/70 bg-brand-50/30 dark:border-brand-500/25 dark:bg-brand-500/5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-ink">外部工具调用</span>
        <Badge tone={failed ? 'amber' : 'emerald'} dot>
          {STATUS_LABEL[invocation.status] ?? invocation.status}
        </Badge>
        {/* 时间戳：步骤有时间而工具调用没有，时间线对不齐 */}
        <span className="ml-auto flex items-center gap-2 text-micro tabular-nums text-ink-faint">
          {new Date(invocation.created_at).toLocaleTimeString('zh-CN')}
          {!running && <span>{invocation.duration_ms} ms</span>}
        </span>
      </div>
      <p className="mt-2 truncate font-mono text-xs text-ink-soft" title={invocation.stable_name}>
        {invocation.server_name} / {invocation.tool_name} · v{invocation.version}
      </p>
      <div className="mt-2 flex flex-wrap gap-1.5 text-micro text-ink-muted">
        <Badge tone="sky">{invocation.stable_name}</Badge>
        {invocation.error_category && <Badge tone="rose">{invocation.error_category}</Badge>}
        {invocation.result_truncated && <Badge tone="orange">输出已截断</Badge>}
      </div>
    </Card>
  )
}
