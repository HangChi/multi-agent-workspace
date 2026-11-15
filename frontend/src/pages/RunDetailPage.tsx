/** 运行详情页（T072，US5）：状态/成本/步骤/制品 + 实时事件 + 取消/重试/回复。 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import * as api from '../api/endpoints'
import { extractError } from '../api/client'
import { StepList } from '../components/run/StepList'
import { ExecutionCard } from '../components/run/ExecutionCard'
import { ExternalToolInvocationCard } from '../components/run/ExternalToolInvocationCard'
import { Badge } from '../components/ui/Badge'
import type { BadgeTone } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, CardHeader, IconTile } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { Icon } from '../components/ui/Icons'
import { Input } from '../components/ui/Field'
import { Page } from '../components/ui/Page'
import { Skeleton } from '../components/ui/Skeleton'
import { useToast } from '../components/ui/Toast'
import { useRunSocket, type RunSocketEvent } from '../hooks/useRunSocket'
import { cn } from '../components/ui/cn'
import { formatBytes } from '../utils/formatBytes'

const STATUS_LABEL: Record<api.RunStatus, string> = {
  queued: '排队中',
  running: '执行中',
  needs_input: '等待输入',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timeout: '超时',
}

const STATUS_TONE: Record<api.RunStatus, BadgeTone> = {
  queued: 'slate',
  running: 'sky',
  needs_input: 'amber',
  completed: 'emerald',
  failed: 'rose',
  cancelled: 'slate',
  timeout: 'orange',
}

const STRATEGY_LABEL: Record<string, string> = {
  direct: '直接路由',
  fan_out: '广播',
  pipeline: '流水线',
  router: '路由器',
}

/** 终态：WS 连接已主动关闭，展示「已结束」而非「未连接」（后者看起来像故障） */
const TERMINAL_STATUSES = new Set<api.RunStatus>(['completed', 'failed', 'cancelled', 'timeout'])

/** 日志级别配色（深色底上的浅色字） */
const LOG_LEVEL_CLASS: Record<string, string> = {
  info: 'text-slate-300',
  warn: 'text-amber-300',
  warning: 'text-amber-300',
  error: 'text-rose-300',
  debug: 'text-slate-500',
}

function MetaTile({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-line/80 bg-surface-muted/70 px-3 py-2">
      <dt className="text-2xs text-ink-faint">{label}</dt>
      <dd className="mt-0.5 truncate text-[13px] font-medium text-ink">{children}</dd>
    </div>
  )
}

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const toast = useToast()
  const [run, setRun] = useState<api.RunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [replyMessage, setReplyMessage] = useState('')
  const [replying, setReplying] = useState(false)
  const [acting, setActing] = useState<'cancel' | 'retry' | null>(null)
  const [logs, setLogs] = useState<{ line: string; level: string }[]>([])
  const logBoxRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    if (!runId) return
    setLoading(true)
    setError(null)
    try {
      setRun(await api.fetchRun(runId))
    } catch (e) {
      setError(extractError(e))
    } finally {
      setLoading(false)
    }
  }, [runId])

  useEffect(() => {
    void load()
  }, [load])

  // 实时事件：合并到当前 run 状态
  const onEvent = useCallback((event: RunSocketEvent) => {
    switch (event.type) {
      case 'status_change':
        setRun((prev) =>
          prev ? { ...prev, status: event.status as api.RunStatus } : prev,
        )
        break
      case 'cost_update':
        setRun((prev) =>
          prev
            ? { ...prev, cost_cents: (event.cost_cents as number) ?? prev.cost_cents }
            : prev,
        )
        break
      case 'step_new':
        setRun((prev) => {
          if (!prev) return prev
          const step = event.step as api.RunStep
          if (prev.steps.some((s) => s.seq === step.seq)) return prev
          return { ...prev, steps: [...prev.steps, step].sort((a, b) => a.seq - b.seq) }
        })
        break
      case 'step_update':
        setRun((prev) => {
          if (!prev) return prev
          const step = event.step as api.RunStep
          return {
            ...prev,
            steps: prev.steps.map((s) => (s.seq === step.seq ? { ...s, ...step } : s)),
          }
        })
        break
      case 'log_line':
        setLogs((prev) => [
          ...prev.slice(-199),
          { line: event.line as string, level: (event.level as string) ?? 'info' },
        ])
        break
      case 'sandbox_started':
        setRun((prev) => {
          if (!prev) return prev
          const execution: api.RunExecution = {
            id: event.execution_id as string,
            step_seq: event.step_seq as number,
            language: event.language as string,
            status: 'running',
            exit_code: null,
            duration_ms: 0,
            stdout_truncated: false,
            artifacts: 0,
          }
          return { ...prev, executions: [...(prev.executions ?? []).filter((item) => item.id !== execution.id), execution] }
        })
        break
      case 'sandbox_finished':
        setRun((prev) => {
          if (!prev) return prev
          return {
            ...prev,
            executions: (prev.executions ?? []).map((item) => item.id === event.execution_id
              ? { ...item, status: event.status as string, exit_code: event.exit_code as number | null, duration_ms: event.duration_ms as number, artifacts: event.artifacts as number }
              : item),
          }
        })
        break
      case 'sandbox_output':
        setLogs((prev) => [...prev.slice(-199), { line: event.chunk as string, level: event.stream === 'stderr' ? 'error' : 'info' }])
        break
      case 'external_tool_call_started':
        setRun((prev) => {
          if (!prev) return prev
          const seq = Number(event.step_seq)
          const stableName = String(event.stable_name ?? 'external-tool')
          const invocation: api.RunExternalToolInvocation = {
            id: `pending-${seq}`,
            step_id: null,
            server_name: 'MCP',
            tool_name: stableName,
            stable_name: stableName,
            version: String(event.version ?? 'unknown'),
            status: 'running',
            duration_ms: 0,
            error_category: null,
            result_truncated: false,
            created_at: new Date().toISOString(),
          }
          return { ...prev, external_tool_invocations: [...(prev.external_tool_invocations ?? []).filter((item) => item.id !== invocation.id), invocation] }
        })
        break
      case 'external_tool_call_update':
      case 'external_tool_policy_denied':
        setRun((prev) => {
          if (!prev) return prev
          const seq = Number(event.step_seq)
          return {
            ...prev,
            external_tool_invocations: (prev.external_tool_invocations ?? []).map((item) => item.id === `pending-${seq}`
              ? { ...item, status: String(event.status ?? 'policy_denied'), duration_ms: Number(event.duration_ms ?? 0), error_category: (event.error_category as string | null) ?? (event.type === 'external_tool_policy_denied' ? 'policy_denied' : null), result_truncated: Boolean(event.result_truncated) }
              : item),
          }
        })
        break
      case 'needs_input':
        setRun((prev) => (prev ? { ...prev, status: 'needs_input' } : prev))
        break
      case 'run_failed':
        setRun((prev) =>
          prev
            ? { ...prev, status: 'failed', error: (event.error as string) ?? prev.error }
            : prev,
        )
        break
      case 'run_completed':
        setRun((prev) =>
          prev
            ? {
                ...prev,
                status: 'completed',
                result: (event.result as Record<string, unknown>) ?? prev.result,
              }
            : prev,
        )
        break
    }
  }, [])

  const { connected } = useRunSocket(runId, onEvent, !!runId)

  // 日志自动滚到底部，跟随最新输出
  useEffect(() => {
    const box = logBoxRef.current
    if (box) box.scrollTop = box.scrollHeight
  }, [logs.length])

  // 操作
  const handleCancel = async () => {
    if (!runId) return
    setActionError(null)
    setActing('cancel')
    try {
      await api.cancelRun(runId)
      setRun((prev) => (prev ? { ...prev, status: 'cancelled' } : prev))
      toast.success('已取消该运行')
    } catch (e) {
      setActionError(extractError(e))
    } finally {
      setActing(null)
    }
  }

  const handleRetry = async () => {
    if (!runId) return
    setActionError(null)
    setActing('retry')
    try {
      await api.retryRun(runId)
      setRun((prev) => (prev ? { ...prev, status: 'queued' } : prev))
      toast.success('已重新入队')
    } catch (e) {
      setActionError(extractError(e))
    } finally {
      setActing(null)
    }
  }

  const handleReply = async () => {
    if (!runId || !replyMessage.trim()) return
    setActionError(null)
    setReplying(true)
    try {
      await api.replyRun(runId, replyMessage.trim())
      setRun((prev) => (prev ? { ...prev, status: 'running' } : prev))
      setReplyMessage('')
      toast.success('已提交补充信息，继续执行')
    } catch (e) {
      setActionError(extractError(e))
    } finally {
      setReplying(false)
    }
  }

  const copyRunId = async () => {
    if (!run) return
    try {
      await window.navigator.clipboard.writeText(run.id)
      toast.success('已复制运行 ID')
    } catch {
      toast.error('复制失败', '请手动选择文本复制')
    }
  }

  if (loading) {
    return (
      <Page width="medium">
        <Skeleton className="h-5 w-24" />
        <Card>
          <div className="flex items-center gap-3">
            <Skeleton className="h-10 w-10 rounded-xl" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-56" />
            </div>
          </div>
          <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14 rounded-lg" />
            ))}
          </div>
        </Card>
        <Card>
          <Skeleton className="h-4 w-24" />
          <div className="mt-4 space-y-2">
            <Skeleton className="h-16 rounded-lg" />
            <Skeleton className="h-16 rounded-lg" />
          </div>
        </Card>
      </Page>
    )
  }

  if (error || !run) {
    return (
      <Page width="narrow">
        <EmptyState
          icon="alert-triangle"
          title="无法加载运行详情"
          description={error ?? '该运行不存在或已被删除'}
          action={
            <div className="flex gap-2">
              <Button variant="secondary" leftIcon="refresh" onClick={() => void load()}>
                重试
              </Button>
              <Link
                to="/"
                className="inline-flex h-10 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-4 text-sm font-medium text-ink-soft shadow-card hover:bg-surface-muted"
              >
                返回看板
              </Link>
            </div>
          }
        />
      </Page>
    )
  }

  const terminal = TERMINAL_STATUSES.has(run.status)

  return (
    <Page width="medium">
      <div className="flex items-center justify-between gap-3">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-sm text-ink-muted transition-colors hover:text-ink"
        >
          <Icon name="arrow-left" className="h-4 w-4" />
          返回看板
        </Link>
        <Badge tone={STATUS_TONE[run.status]} dot pulse={run.status === 'running'} size="md">
          {STATUS_LABEL[run.status]}
        </Badge>
      </div>

      {/* 概要 */}
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <IconTile tone="brand" size="lg">
              <Icon name="play" className="h-5 w-5" />
            </IconTile>
            <div className="min-w-0">
              <h1 className="text-lg font-semibold tracking-tight text-ink">
                运行 {run.id.slice(0, 8)}
              </h1>
              <p className="mt-0.5 text-xs text-ink-faint">
                {run.assignee_type === 'agent' ? 'Agent' : '组'}{' '}
                {run.assignee_id.slice(0, 8)} · 请求 {run.request_id.slice(0, 8)} ·{' '}
                {new Date(run.created_at).toLocaleString('zh-CN')}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="ghost" leftIcon="copy" onClick={() => void copyRunId()}>
              复制 ID
            </Button>
            {!terminal && run.status !== 'needs_input' && (
              <Button
                size="sm"
                variant="ghost"
                leftIcon="x-circle"
                loading={acting === 'cancel'}
                onClick={() => void handleCancel()}
                className="text-rose-600 hover:bg-rose-50 hover:text-rose-700 dark:text-rose-300 dark:hover:bg-rose-500/15"
              >
                取消运行
              </Button>
            )}
            {(run.status === 'failed' || run.status === 'timeout') && (
              <Button
                size="sm"
                variant="success"
                leftIcon="refresh"
                loading={acting === 'retry'}
                onClick={() => void handleRetry()}
              >
                重试
              </Button>
            )}
          </div>
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <MetaTile label="策略">{STRATEGY_LABEL[run.strategy] ?? run.strategy}</MetaTile>
          <MetaTile label="当前步骤">
            {run.current_step > 0 ? `第 ${run.current_step} 步` : '未开始'}
          </MetaTile>
          <MetaTile label="累计成本">
            <span className="tabular-nums">¥{(run.cost_cents / 100).toFixed(2)}</span>
          </MetaTile>
          <MetaTile label="实时连接">
            {connected ? (
              <span className="inline-flex items-center gap-1.5 text-emerald-600">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse-ring" />
                已连接
              </span>
            ) : TERMINAL_STATUSES.has(run.status) ? (
              <span className="text-ink-faint">已结束</span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-ink-faint">
                <span className="h-1.5 w-1.5 rounded-full bg-line-strong" />
                未连接
              </span>
            )}
          </MetaTile>
          <div className="col-span-2 text-xs sm:col-span-4">
            <dt className="inline text-ink-muted">工具授权快照：</dt>
            <dd className="inline break-words text-ink">
              {run.tool_grants_snapshot == null
                ? '未记录（旧版或待路由运行）'
                : run.tool_grants_snapshot.length === 0
                  ? '无工具'
                  : run.tool_grants_snapshot.join('、')}
            </dd>
          </div>
        </dl>

        {actionError && (
          <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600 dark:bg-rose-500/10 dark:text-rose-300" role="alert">
            <Icon name="alert-circle" className="mt-0.5 h-4 w-4 shrink-0" />
            {actionError}
          </p>
        )}

        {run.error && (
          <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50/80 p-3 text-sm text-rose-700 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-300">
            <p className="flex items-center gap-1.5 font-medium">
              <Icon name="alert-triangle" className="h-4 w-4" />
              执行失败
            </p>
            <p className="mt-1 whitespace-pre-wrap break-words leading-relaxed">{run.error}</p>
          </div>
        )}

        {run.result && (
          <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50/70 p-3 dark:border-emerald-500/25 dark:bg-emerald-500/10">
            <div className="flex items-center justify-between">
              <p className="flex items-center gap-1.5 text-sm font-medium text-emerald-800 dark:text-emerald-300">
                <Icon name="check-circle" className="h-4 w-4" />
                执行结果
              </p>
            </div>
            <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-emerald-900 dark:text-emerald-200">
              {JSON.stringify(run.result, null, 2)}
            </pre>
          </div>
        )}
      </Card>

      {/* 等待输入：把回复框做成独立卡片，比塞在标题栏更清晰 */}
      {run.status === 'needs_input' && (
        <Card tone="amber">
          <div className="flex items-start gap-2.5">
            <IconTile tone="amber" size="sm">
              <Icon name="alert-circle" className="h-3.5 w-3.5" />
            </IconTile>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-amber-900">Agent 需要补充信息才能继续</p>
              <p className="mt-0.5 text-xs text-amber-700/80">
                填写后点击「回复并继续」，运行会从当前步骤接着执行。
              </p>
              <div className="mt-2.5 flex flex-col gap-2 sm:flex-row">
                <Input
                  value={replyMessage}
                  onChange={(e) => setReplyMessage(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                      e.preventDefault()
                      void handleReply()
                    }
                  }}
                  placeholder="例如：请使用 2026 年 Q1 的数据，输出 Markdown 表格"
                  wrapperClassName="flex-1"
                  aria-label="补充信息"
                />
                <Button
                  variant="warning"
                  leftIcon="send"
                  loading={replying}
                  disabled={!replyMessage.trim()}
                  onClick={() => void handleReply()}
                >
                  回复并继续
                </Button>
              </div>
            </div>
          </div>
        </Card>
      )}

      {/* 步骤 */}
      <Card>
        <CardHeader
          title="执行步骤"
          description={terminal ? '运行已结束' : '步骤随执行实时追加'}
          icon={
            <IconTile tone="slate" size="sm">
              <Icon name="layers" className="h-3.5 w-3.5" />
            </IconTile>
          }
          actions={<Badge tone="slate">{run.steps.length} 步</Badge>}
          className="mb-4"
        />
        <StepList steps={run.steps} />
      </Card>

      {(run.executions ?? []).length > 0 && (
        <div className="space-y-2">
          {(run.executions ?? []).map((execution) => (
            <ExecutionCard key={execution.id} runId={run.id} execution={execution} />
          ))}
        </div>
      )}

      {(run.external_tool_invocations ?? []).length > 0 && (
        <div className="space-y-2" aria-label="外部工具调用">
          {(run.external_tool_invocations ?? []).map((invocation) => (
            <ExternalToolInvocationCard key={invocation.id} invocation={invocation} />
          ))}
        </div>
      )}

      {/* 制品 */}
      {run.artifacts.length > 0 && (
        <Card>
          <CardHeader
            title="制品"
            description="运行产出的文件，可直接下载"
            icon={
              <IconTile tone="emerald" size="sm">
                <Icon name="file" className="h-3.5 w-3.5" />
              </IconTile>
            }
            actions={<Badge tone="emerald">{run.artifacts.length}</Badge>}
            className="mb-3"
          />
          <ul className="space-y-1.5">
            {run.artifacts.map((art) => (
              <li
                key={art.id}
                className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 transition-colors hover:border-line-strong hover:bg-surface-muted/60"
              >
                <Icon name="file" className="h-4 w-4 shrink-0 text-ink-faint" />
                <span className="min-w-0 flex-1 truncate text-[13px] text-ink-soft">
                  {art.filename}
                </span>
                <span className="shrink-0 text-2xs tabular-nums text-ink-faint">
                  {formatBytes(art.size_bytes)}
                </span>
                <a
                  href={api.artifactDownloadUrl(run.id, art.id)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex shrink-0 items-center gap-1 rounded-md border border-line-strong bg-surface px-2 py-1 text-2xs font-medium text-ink-soft transition-colors hover:border-line-strong hover:text-ink"
                >
                  <Icon name="download" className="h-3 w-3" />
                  下载
                </a>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* 日志 */}
      <Card padding="none" tone="dark" className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <Icon name="file" className="h-3.5 w-3.5 text-slate-400" />
            <span className="text-xs font-medium text-slate-200">运行日志</span>
            <span className="rounded bg-white/10 px-1.5 py-0.5 text-2xs tabular-nums text-slate-300">
              {logs.length}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 text-2xs text-slate-500">
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  connected ? 'bg-emerald-400' : 'bg-slate-600',
                )}
              />
              {connected ? '实时接收中' : '未连接'}
            </span>
            {logs.length > 0 && (
              <button
                type="button"
                onClick={() => setLogs([])}
                className="rounded px-1.5 py-0.5 text-2xs text-slate-400 transition-colors hover:bg-white/10 hover:text-slate-200"
              >
                清空
              </button>
            )}
          </div>
        </div>
        <div
          ref={logBoxRef}
          className="max-h-72 overflow-y-auto px-3 py-2.5 font-mono text-xs leading-relaxed"
        >
          {logs.length === 0 ? (
            <p className="py-4 text-center text-ink-muted">
              {terminal ? '本次运行没有日志输出' : '等待日志…'}
            </p>
          ) : (
            logs.map((log, i) => (
              <p key={i} className="flex gap-2 py-0.5">
                <span className="shrink-0 select-none text-slate-600">
                  {String(i + 1).padStart(3, '0')}
                </span>
                <span className="shrink-0 uppercase opacity-70">{log.level.padEnd(5, ' ')}</span>
                <span className={cn('min-w-0 whitespace-pre-wrap break-words', LOG_LEVEL_CLASS[log.level] ?? 'text-slate-300')}>
                  {log.line}
                </span>
              </p>
            ))
          )}
        </div>
      </Card>
    </Page>
  )
}
