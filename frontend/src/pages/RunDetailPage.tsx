/** 运行详情页（T072，US5）：状态/成本/步骤/制品 + 实时事件 + 取消/重试/回复。 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import * as api from '../api/endpoints'
import { extractError } from '../api/client'
import { StepList } from '../components/run/StepList'
import { ExecutionCard } from '../components/run/ExecutionCard'
import { ExternalToolInvocationCard } from '../components/run/ExternalToolInvocationCard'
import {
  RETRY_STATUS_META,
  formatRetryCountdown,
  retryProgressText,
} from '../components/run/RetryStatus'
import { Badge } from '../components/ui/Badge'
import type { BadgeTone } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, CardHeader, IconTile } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { Icon } from '../components/ui/Icons'
import { Input } from '../components/ui/Field'
import { Page } from '../components/ui/Page'
import { Skeleton } from '../components/ui/Skeleton'
import { useConfirm } from '../components/ui/useConfirm'
import { useToast } from '../components/ui/useToast'
import { LogPanel } from '../components/run/LogPanel'
import { formatCostYuan } from '../utils/formatCost'
import { useRunSocket, type RunSocketEvent } from '../hooks/useRunSocket'
import { formatBytes } from '../utils/formatBytes'

/**
 * 006：距下次重试的剩余秒数，每秒刷新。
 *
 * 用本地计算而不是轮询后端：`next_retry_at` 一次性写入后不会变，轮询只会给
 * API 添负载。WS 的 `run_retrying` 到达时 next_retry_at 被重置，这里自然重新起算。
 */
function useRetryCountdown(nextRetryAt: string | null): number | null {
  const [secondsLeft, setSecondsLeft] = useState(() => formatRetryCountdown(nextRetryAt))
  useEffect(() => {
    setSecondsLeft(formatRetryCountdown(nextRetryAt))
    if (!nextRetryAt) return
    const timer = window.setInterval(() => setSecondsLeft(formatRetryCountdown(nextRetryAt)), 1000)
    return () => window.clearInterval(timer)
  }, [nextRetryAt])
  return secondsLeft
}

// 006：状态元数据收敛到 RetryStatus.tsx（含 retrying 与未知状态降级），
// 避免页面里各写一份而漏掉新状态。
const STATUS_LABEL: Record<api.RunStatus, string> = Object.fromEntries(
  (Object.keys(RETRY_STATUS_META) as api.RunStatus[]).map((k) => [k, RETRY_STATUS_META[k].label])
) as Record<api.RunStatus, string>
const STATUS_TONE: Record<api.RunStatus, BadgeTone> = Object.fromEntries(
  (Object.keys(RETRY_STATUS_META) as api.RunStatus[]).map((k) => [k, RETRY_STATUS_META[k].tone])
) as Record<api.RunStatus, BadgeTone>

const STRATEGY_LABEL: Record<string, string> = {
  direct: '直接路由',
  fan_out: '广播',
  pipeline: '流水线',
  router: '路由器',
}

/** 终态：WS 连接已主动关闭，展示「已结束」而非「未连接」（后者看起来像故障） */
const TERMINAL_STATUSES = new Set<api.RunStatus>(['completed', 'failed', 'cancelled', 'timeout'])

/** 006：「第 2/3 次 · 约 4s 后重试」——重试等待中的进度提示。 */
function RetryProgress({ attempt, nextRetryAt }: { attempt: number; nextRetryAt: string | null }) {
  const secondsLeft = useRetryCountdown(nextRetryAt)
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700 ring-1 ring-inset ring-amber-200/70 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/25"
      data-testid="retry-progress"
    >
      <Icon name="clock" className="h-3.5 w-3.5" />
      {retryProgressText({ attempt, maxAttempts: null, secondsLeft })}
    </span>
  )
}

function MetaTile({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line/80 bg-surface-muted/70 px-3 py-2">
      <dt className="text-micro text-ink-faint">{label}</dt>
      <dd className="mt-0.5 truncate text-ui font-medium text-ink">{children}</dd>
    </div>
  )
}

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const toast = useToast()
  const confirm = useConfirm()
  const [run, setRun] = useState<api.RunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [replyMessage, setReplyMessage] = useState('')
  const [replying, setReplying] = useState(false)
  const [acting, setActing] = useState<'cancel' | 'retry' | null>(null)
  const [logs, setLogs] = useState<{ line: string; level: string; at: string }[]>([])

  const load = useCallback(async () => {
    if (!runId) return
    setLoading(true)
    setError(null)
    try {
      setRun(await api.fetchRun(runId))
      // 切 run（路由参数变化，组件复用）时必须清空上一 run 的日志与
      // 回复草稿：否则旧日志会混在新 run 日志顶部，直到被挤出
      setLogs([])
      setReplyMessage('')
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
        setRun((prev) => (prev ? { ...prev, status: event.status as api.RunStatus } : prev))
        break
      case 'cost_update':
        setRun((prev) =>
          prev ? { ...prev, cost_cents: (event.cost_cents as number) ?? prev.cost_cents } : prev
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
          { line: event.line as string, level: (event.level as string) ?? 'info', at: new Date().toISOString() },
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
          return {
            ...prev,
            executions: [
              ...(prev.executions ?? []).filter((item) => item.id !== execution.id),
              execution,
            ],
          }
        })
        break
      case 'sandbox_finished':
        setRun((prev) => {
          if (!prev) return prev
          return {
            ...prev,
            executions: (prev.executions ?? []).map((item) =>
              item.id === event.execution_id
                ? {
                    ...item,
                    status: event.status as string,
                    exit_code: event.exit_code as number | null,
                    duration_ms: event.duration_ms as number,
                    artifacts: event.artifacts as number,
                  }
                : item
            ),
          }
        })
        break
      case 'sandbox_output':
        setLogs((prev) => [
          ...prev.slice(-199),
          {
            line: event.chunk as string,
            level: event.stream === 'stderr' ? 'error' : 'info',
            at: new Date().toISOString(),
          },
        ])
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
          return {
            ...prev,
            external_tool_invocations: [
              ...(prev.external_tool_invocations ?? []).filter((item) => item.id !== invocation.id),
              invocation,
            ],
          }
        })
        break
      case 'external_tool_call_update':
      case 'external_tool_policy_denied':
        setRun((prev) => {
          if (!prev) return prev
          const seq = Number(event.step_seq)
          return {
            ...prev,
            external_tool_invocations: (prev.external_tool_invocations ?? []).map((item) =>
              item.id === `pending-${seq}`
                ? {
                    ...item,
                    status: String(event.status ?? 'policy_denied'),
                    duration_ms: Number(event.duration_ms ?? 0),
                    error_category:
                      (event.error_category as string | null) ??
                      (event.type === 'external_tool_policy_denied' ? 'policy_denied' : null),
                    result_truncated: Boolean(event.result_truncated),
                  }
                : item
            ),
          }
        })
        break
      case 'needs_input':
        setRun((prev) => (prev ? { ...prev, status: 'needs_input' } : prev))
        break
      case 'run_retrying': {
        // 006：瞬时失败后进入重试等待。attempt 是「即将开始的第几次」，
        // 与后端 events.run_retrying 的语义一致。
        setRun((prev) =>
          prev
            ? {
                ...prev,
                status: 'retrying',
                attempt: (event.attempt as number) ?? prev.attempt,
                next_retry_at: (event.next_retry_at as string) ?? null,
              }
            : prev
        )
        break
      }
      case 'run_failed':
        setRun((prev) =>
          prev ? { ...prev, status: 'failed', error: (event.error as string) ?? prev.error } : prev
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
            : prev
        )
        break
    }
  }, [])

  const { connected, status: socketStatus, retryCount, reconnect } = useRunSocket(
    runId,
    onEvent,
    !!runId
  )

  /**
   * 重连成功后补数：断线期间的 step/log 事件不会补发，connected 由
   * false→true 的跳变就是「该重拉了」的信号。
   */
  const wasConnected = useRef(connected)
  useEffect(() => {
    if (connected && !wasConnected.current) void load()
    wasConnected.current = connected
  }, [connected, load])

  /**
   * 重连耗尽后的降级轮询：连接彻底放弃时页面不能永久停留在过期状态
   * （run 早已 completed，页面仍显示「执行中」是最糟的谎言）。
   */
  useEffect(() => {
    if (socketStatus !== 'failed') return
    const timer = window.setInterval(() => void load(), 10_000)
    return () => window.clearInterval(timer)
  }, [socketStatus, load])

  // 操作
  const handleCancel = async () => {
    if (!runId) return
    // 取消会丢弃全部进行中的工作——破坏性操作，先确认
    const ok = await confirm({
      title: '取消该运行？',
      description: '已执行的步骤与成本会保留，但任务不会继续。取消后如需重跑，可点「重试」重新入队。',
      confirmText: '取消运行',
      tone: 'warning',
    })
    if (!ok) return
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

  /** 制品下载：先带鉴权换预签名 URL，再跳转（直链会 401）。 */
  const downloadArtifact = async (artifactId: string) => {
    if (!run) return
    try {
      const { url } = await api.resolveArtifactDownloadUrl(run.id, artifactId)
      api.openDownloadUrl(url)
    } catch (e) {
      toast.error('下载失败', extractError(e))
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
              <Button as="link" to="/" variant="secondary" leftIcon="arrow-left">
                返回看板
              </Button>
            </div>
          }
        />
      </Page>
    )
  }

  const terminal = TERMINAL_STATUSES.has(run.status)

  return (
    <Page width="wide">
      <div className="flex items-center justify-between gap-3">
        <Link
          to="/"
          className="inline-flex h-9 items-center gap-1.5 rounded-lg px-1.5 text-ui text-ink-muted transition-colors hover:bg-surface-subtle hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45"
        >
          <Icon name="arrow-left" className="h-4 w-4" />
          返回看板
        </Link>
        <div className="flex items-center gap-2">
          {run.status === 'retrying' && (
            <RetryProgress attempt={run.attempt} nextRetryAt={run.next_retry_at} />
          )}
          <Badge tone={STATUS_TONE[run.status]} dot pulse={run.status === 'running'} size="md">
            {STATUS_LABEL[run.status]}
          </Badge>
        </div>
      </div>

      {/* 概要 */}
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <Icon name="play" />
            <div className="min-w-0">
              <p className="label-caps mb-1">运行详情</p>
              <h1 className="font-mono text-lg font-semibold tracking-tight text-ink">
                {run.id.slice(0, 8)}
                <span className="text-ink-faint">…{run.id.slice(-4)}</span>
              </h1>
              <p className="mt-0.5 text-xs text-ink-faint">
                {run.assignee_type === 'agent' ? 'Agent' : '组'} {run.assignee_id.slice(0, 8)} ·
                请求 {run.request_id.slice(0, 8)} ·{' '}
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
                tone="danger"
                leftIcon="x-circle"
                loading={acting === 'cancel'}
                onClick={() => void handleCancel()}
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
            <span className="tabular-nums">¥{formatCostYuan(run.cost_cents, run.cost_micros)}</span>
          </MetaTile>
          {run.attempt > 1 && <MetaTile label="尝试次数">第 {run.attempt} 次</MetaTile>}
          <MetaTile label="实时连接">
            {socketStatus === 'connected' ? (
              <span className="inline-flex items-center gap-1.5 text-emerald-600 dark:text-emerald-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse-ring" />
                已连接
              </span>
            ) : TERMINAL_STATUSES.has(run.status) ? (
              <span className="text-ink-faint">已结束</span>
            ) : socketStatus === 'reconnecting' ? (
              <span className="inline-flex items-center gap-1.5 text-amber-600 dark:text-amber-300">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                重连中（第 {retryCount} 次）
              </span>
            ) : socketStatus === 'failed' ? (
              <span className="inline-flex items-center gap-1.5 text-rose-600 dark:text-rose-300">
                <span className="h-1.5 w-1.5 rounded-full bg-rose-500" />
                连接中断（已降级轮询）
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-ink-faint">
                <span className="h-1.5 w-1.5 rounded-full bg-line-strong" />
                连接中…
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
          <p
            className="mt-3 flex items-start gap-1.5 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600 dark:bg-rose-500/10 dark:text-rose-300"
            role="alert"
          >
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

      {/* 006：重试历史（FR-017：每次尝试的败因都可查） */}

      {run.retry_history.length > 0 && (
        <Card>
          <CardHeader
            title="重试历史"

            description="每次失败的原因与时刻；自动重试与手动重试都记录在此"
          />

          <ol className="space-y-2">
            {[...run.retry_history].reverse().map((entry) => (
              <li
                key={entry.attempt}

                className="flex items-start gap-2.5 rounded-lg border border-line-subtle bg-surface-muted/60 p-3"
              >
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-100 text-micro font-medium text-amber-700 dark:bg-amber-500/15 dark:text-amber-300">
                  {entry.attempt}
                </span>

                <div className="min-w-0 flex-1">
                  <p className="text-ui leading-relaxed text-ink">{entry.error}</p>

                  <p className="mt-1 text-micro text-ink-faint">
                    {new Date(entry.at).toLocaleString('zh-CN')}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </Card>
      )}

      {/* 结果与制品并排：一个运行最想先看到的两件事，别让用户翻两次 */}
      {(run.result || run.artifacts.length > 0) && (
        <div className="grid gap-4 lg:grid-cols-2">
          {run.result && (
            <Card padding="none" className="overflow-hidden">
              <div className="flex items-center gap-2 border-b border-emerald-200/70 bg-emerald-50/70 px-4 py-2.5 dark:border-emerald-500/25 dark:bg-emerald-500/10">
                <Icon
                  name="check-circle"
                  className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-300"
                />
                <span className="text-ui font-medium text-emerald-800 dark:text-emerald-300">
                  执行结果
                </span>
              </div>
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-xs leading-relaxed text-ink-soft">
                {JSON.stringify(run.result, null, 2)}
              </pre>
            </Card>
          )}

          {run.artifacts.length > 0 && (
            <Card padding="none" className="overflow-hidden">
              <div className="flex items-center justify-between gap-2 border-b border-line-subtle px-4 py-2.5">
                <span className="flex items-center gap-2">
                  <Icon name="file" className="h-3.5 w-3.5 text-ink-faint" />
                  <span className="text-ui font-medium text-ink">制品</span>
                  <span className="text-micro font-normal text-ink-faint">可直接下载</span>
                </span>
                <Badge tone="emerald">{run.artifacts.length}</Badge>
              </div>
              <ul className="divide-y divide-line-subtle">
                {run.artifacts.map((art) => (
                  <li
                    key={art.id}
                    className="flex items-center gap-2.5 px-4 py-2.5 transition-colors hover:bg-surface-muted/60"
                  >
                    <Icon name="file" className="h-4 w-4 shrink-0 text-ink-faint" />
                    <span className="min-w-0 flex-1 truncate text-ui text-ink-soft">
                      {art.filename}
                    </span>
                    <span className="shrink-0 text-micro tabular-nums text-ink-faint">
                      {formatBytes(art.size_bytes)}
                    </span>
                    <Button
                      size="xs"
                      variant="secondary"
                      leftIcon="download"
                      onClick={() => void downloadArtifact(art.id)}
                    >
                      下载
                    </Button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
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

      {/* 日志 */}
      <LogPanel
        logs={logs}
        socketLabel={
          socketStatus === 'connected'
            ? '实时接收中'
            : socketStatus === 'connecting'
              ? '连接中…'
              : socketStatus === 'reconnecting'
                ? `重连中（第 ${retryCount}/${5} 次）…`
                : '连接已中断'
        }
        socketConnected={connected}
        onClear={() => {
          setLogs([])
          toast.info('已清空日志显示', '仅清空当前页面缓冲，服务端日志不受影响')
        }}
      />
      {socketStatus === 'failed' && !terminal && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-300">
          <Icon name="alert-triangle" className="h-3.5 w-3.5 shrink-0" />
          实时连接多次重连失败，已切换为每 10 秒轮询（可能看不到最新进度）。
          <Button size="xs" variant="warning" leftIcon="refresh" onClick={reconnect} className="ml-auto">
            重新连接
          </Button>
        </div>
      )}
    </Page>
  )
}
