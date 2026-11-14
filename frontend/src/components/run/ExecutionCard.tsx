import { useState } from 'react'

import { apiClient } from '../../api/client'
import type { ExecutionDetail as ExecutionDetailType } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
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

export function ExecutionCard({
  runId,
  execution,
}: {
  runId: string
  execution: ExecutionSummary
}) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState<ExecutionDetailType | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const failed = execution.status !== 'ok' && execution.status !== 'running'

  const toggle = async () => {
    const next = !expanded
    setExpanded(next)
    if (!next || detail || loading) return
    setLoading(true)
    setError(null)
    try {
      const { data } = await apiClient.get<ExecutionDetailType>(
        `/runs/${runId}/executions/${execution.id}`,
      )
      setDetail(data)
    } catch {
      setError('执行详情加载失败，请稍后重试。')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-ink">沙箱执行 · {execution.language}</span>
          <Badge tone={failed ? 'rose' : execution.status === 'running' ? 'sky' : 'emerald'}>
            {execution.status}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-faint">第 {execution.step_seq} 步</span>
          <Button size="sm" variant="ghost" onClick={() => void toggle()}>
            {expanded ? '收起详情' : '查看代码与输出'}
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs text-ink-muted sm:grid-cols-4">
        <span>退出码：{execution.exit_code ?? '—'}</span>
        <span>耗时：{execution.duration_ms} ms</span>
        <span>制品：{execution.artifacts}</span>
        {execution.stdout_truncated && <span className="text-amber-600">输出已截断</span>}
      </div>
      {expanded && (
        <div className="space-y-3 border-t border-line pt-3">
          {loading && <p className="text-xs text-ink-muted">正在加载执行详情…</p>}
          {error && <p className="text-xs text-rose-600">{error}</p>}
          {detail?.error_message && (
            <p className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700">{detail.error_message}</p>
          )}
          {detail && (
            <>
              <CodePanel title="脚本" value={detail.code} />
              <div className="grid gap-3 md:grid-cols-2">
                <CodePanel title="stdout" value={detail.stdout ?? ''} />
                <CodePanel title="stderr" value={detail.stderr ?? ''} />
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-faint">
                <span>CPU：{detail.cpu_ms == null ? '未采集' : `${detail.cpu_ms} ms`}</span>
                <span>内存峰值：{detail.mem_peak_bytes == null ? '未采集' : `${(detail.mem_peak_bytes / 1048576).toFixed(1)} MB`}</span>
                <span>镜像：{detail.image}</span>
                {detail.stdout_truncated && <span>stdout 原始字节：{detail.stdout_full_bytes}</span>}
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  )
}

function CodePanel({ title, value }: { title: string; value: string }) {
  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-line">
      <h3 className="border-b border-line bg-surface-muted px-3 py-2 text-xs font-medium text-ink-soft">
        {title}
      </h3>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-xs leading-relaxed text-ink">
        {value || '（无输出）'}
      </pre>
    </section>
  )
}
