import { useCallback, useEffect, useState } from 'react'

import {
  fetchExecution,
  fetchExecutionEgress,
  type EgressAccess,
  type ExecutionDetail as ExecutionDetailType,
  openDownloadUrl,
  resolveArtifactDownloadUrl,
} from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { formatBytes } from '../../utils/formatBytes'

export interface ExecutionSummary {
  id: string
  step_seq: number
  language: string
  status: string
  exit_code: number | null
  duration_ms: number
  stdout_truncated: boolean
  artifacts: number
  error_category?: string | null
}

const FAILURE_LABEL: Record<string, string> = {
  timeout: '超时', oom: '内存超限', pids: '进程数超限', disk: '磁盘超限',
  policy: '策略拦截', infra: '基础设施异常', cancelled: '已取消', exit_code: '脚本失败',
  resource: '资源受限',
}

const ARTIFACT_LIMIT_LABEL: Record<string, string> = {
  files: '制品数量达到上限',
  file_size: '单个制品大小超过上限',
  total_size: '制品总大小达到上限',
}

export function ExecutionCard({ runId, execution }: { runId: string; execution: ExecutionSummary }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState<ExecutionDetailType | null>(null)
  const [egress, setEgress] = useState<EgressAccess[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const failed = execution.status !== 'ok' && execution.status !== 'running'
  const status = detail?.status ?? execution.status
  const failureCategory = detail?.error_category ?? execution.error_category

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [nextDetail, nextEgress] = await Promise.all([
        fetchExecution(runId, execution.id),
        fetchExecutionEgress(runId, execution.id),
      ])
      setDetail(nextDetail)
      setEgress(nextEgress?.items ?? [])
    } catch {
      setError('执行详情加载失败，请稍后重试。')
    } finally { setLoading(false) }
  }, [execution.id, runId])

  const toggle = async () => {
    const next = !expanded
    setExpanded(next)
    if (next && !detail) await refresh()
  }

  useEffect(() => {
    if (!expanded || status !== 'running') return
    const timer = window.setInterval(() => { void refresh() }, 2500)
    return () => window.clearInterval(timer)
  }, [expanded, refresh, status])

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-ink">沙箱执行 · {execution.language}</span>
          <Badge tone={failed ? 'rose' : status === 'running' ? 'sky' : 'emerald'}>{status}</Badge>
          {failureCategory && <Badge tone="rose">{FAILURE_LABEL[failureCategory] ?? failureCategory}</Badge>}
          {detail && <Badge tone="slate">资源</Badge>}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-ink-faint">第 {execution.step_seq} 步</span>
          {status === 'running' && <Button size="sm" variant="ghost" loading={loading} onClick={() => { setExpanded(true); void refresh() }}>刷新执行</Button>}
          <Button size="sm" variant="ghost" onClick={() => void toggle()}>{expanded ? '收起详情' : '查看代码与输出'}</Button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs text-ink-muted sm:grid-cols-4">
        <span>退出码：{detail?.exit_code ?? execution.exit_code ?? '—'}</span>
        <span>耗时：{detail?.duration_ms ?? execution.duration_ms} ms</span>
        <span>制品：{detail?.artifacts.length ?? execution.artifacts}</span>
        {detail?.cpu_ms != null && <span>CPU：{detail.cpu_ms} ms</span>}
        {detail?.mem_peak_bytes != null && <span>内存峰值：{formatBytes(detail.mem_peak_bytes)}</span>}
        {execution.stdout_truncated && <span className="text-amber-600">输出已截断</span>}
      </div>
      {expanded && (
        <div className="space-y-3 border-t border-line pt-3">
          {loading && !detail && <p className="text-xs text-ink-muted">正在加载执行详情…</p>}
          {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
          {detail?.error_message && <p className="rounded-lg bg-rose-50 p-3 text-xs text-rose-700">{detail.error_message}</p>}
          {detail && <>
            <CodePanel title="脚本" value={detail.code} />
            <div className="grid gap-3 md:grid-cols-2"><CodePanel title="stdout" value={detail.stdout ?? ''} /><CodePanel title="stderr" value={detail.stderr ?? ''} /></div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-faint">
              <span>镜像：{detail.image}</span>
              <span>CPU：{detail.cpu_ms == null ? '未采集' : `${detail.cpu_ms} ms`}</span>
              <span>内存峰值：{detail.mem_peak_bytes == null ? '未采集' : formatBytes(detail.mem_peak_bytes)}</span>
              {detail.stdout_truncated && <span>stdout 原始字节：{detail.stdout_full_bytes}</span>}
            </div>
            {detail.artifact_truncated && <p role="status" aria-label="制品截断提示" className="break-words rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              <span className="font-medium">制品已截断：{ARTIFACT_LIMIT_LABEL[detail.artifact_truncated.reason] ?? '制品回收达到限制'}。</span>
              {' '}{detail.artifact_truncated.message} 部分文件未回收，已回收的制品仍可下载。
            </p>}
            {detail.artifacts.length > 0 && <ArtifactList runId={runId} artifacts={detail.artifacts} />}
            <EgressList items={egress} />
          </>}
        </div>
      )}
    </Card>
  )
}

function ArtifactList({ runId, artifacts }: { runId: string; artifacts: ExecutionDetailType['artifacts'] }) {
  // 直链拿不到鉴权：先换预签名 URL 再跳转
  const download = async (artifactId: string) => {
    const { url } = await resolveArtifactDownloadUrl(runId, artifactId)
    openDownloadUrl(url)
  }
  return (
    <section aria-label="执行制品" className="space-y-1.5">
      <h3 className="text-xs font-medium text-ink-soft">执行制品</h3>
      <ul className="space-y-1">
        {artifacts.map((artifact) => (
          <li key={artifact.id} className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-xs">
            <span className="min-w-0 flex-1 truncate text-ink" title={artifact.filename}>{artifact.filename}</span>
            <span className="text-ink-faint">{formatBytes(artifact.size_bytes)}</span>
            <button
              type="button"
              className="rounded border border-line-strong px-2 py-1 text-ink-soft hover:text-ink"
              onClick={() => void download(artifact.id)}
            >
              下载
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function EgressList({ items }: { items: EgressAccess[] }) {
  return <section aria-label="出站访问记录" className="space-y-1.5"><h3 className="text-xs font-medium text-ink-soft">访问记录 <span className="text-ink-faint">({items.length})</span></h3>{items.length === 0 ? <p className="text-xs text-ink-faint">暂无出站访问记录。</p> : <ul className="space-y-1">{items.map((item, index) => <li key={`${item.occurred_at}-${index}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line px-3 py-2 text-xs"><Badge tone={item.action === 'deny' ? 'rose' : 'emerald'}>{item.action === 'deny' ? '拦截' : '放行'}</Badge><span className="font-mono text-ink">{item.target_domain ?? 'ip-direct'} ({item.target_ip}:{item.port})</span><span className="text-ink-faint">{item.bytes_out == null ? '—' : formatBytes(item.bytes_out)}</span></li>)}</ul>}</section>
}

function CodePanel({ title, value }: { title: string; value: string }) {
  return <section className="min-w-0 overflow-hidden rounded-lg border border-line"><h3 className="border-b border-line bg-surface-muted px-3 py-2 text-xs font-medium text-ink-soft">{title}</h3><pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-xs leading-relaxed text-ink">{value || '（无输出）'}</pre></section>
}
