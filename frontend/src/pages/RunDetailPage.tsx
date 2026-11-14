/** 运行详情页（T072，US5）：状态/成本/步骤/制品 + 实时事件 + 取消/重试/回复。 */

import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import * as api from '../api/endpoints'
import { extractError } from '../api/client'
import { StepList } from '../components/run/StepList'
import { useRunSocket, type RunSocketEvent } from '../hooks/useRunSocket'

const STATUS_LABEL: Record<api.RunStatus, string> = {
  queued: '排队中',
  running: '执行中',
  needs_input: '等待输入',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timeout: '超时',
}

const STATUS_STYLE: Record<api.RunStatus, string> = {
  queued: 'bg-slate-100 text-slate-600',
  running: 'bg-blue-100 text-blue-700',
  needs_input: 'bg-amber-100 text-amber-700',
  completed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-slate-200 text-slate-500',
  timeout: 'bg-orange-100 text-orange-700',
}

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const [run, setRun] = useState<api.RunDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [replyMessage, setReplyMessage] = useState('')
  const [logs, setLogs] = useState<{ line: string; level: string }[]>([])

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

  // 操作
  const handleCancel = async () => {
    if (!runId) return
    setActionError(null)
    try {
      await api.cancelRun(runId)
      setRun((prev) => (prev ? { ...prev, status: 'cancelled' } : prev))
    } catch (e) {
      setActionError(extractError(e))
    }
  }

  const handleRetry = async () => {
    if (!runId) return
    setActionError(null)
    try {
      await api.retryRun(runId)
      setRun((prev) => (prev ? { ...prev, status: 'queued' } : prev))
    } catch (e) {
      setActionError(extractError(e))
    }
  }

  const handleReply = async () => {
    if (!runId || !replyMessage.trim()) return
    setActionError(null)
    try {
      await api.replyRun(runId, replyMessage.trim())
      setRun((prev) => (prev ? { ...prev, status: 'running' } : prev))
      setReplyMessage('')
    } catch (e) {
      setActionError(extractError(e))
    }
  }

  if (loading) {
    return <p className="text-slate-500">加载运行详情...</p>
  }
  if (error || !run) {
    return (
      <div className="rounded-lg bg-red-50 p-4 text-sm text-red-600">
        {error ?? '运行不存在'}
      </div>
    )
  }

  const terminal = ['completed', 'failed', 'cancelled', 'timeout'].includes(run.status)

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <Link to="/" className="text-sm text-slate-400 hover:text-slate-600">
          ← 返回看板
        </Link>
        <span
          className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_STYLE[run.status]}`}
        >
          {STATUS_LABEL[run.status]}
        </span>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold text-slate-800">
            运行 {run.id.slice(0, 8)}
          </h1>
          {!terminal && (
            <div className="flex gap-2">
              {run.status === 'needs_input' ? (
                <>
                  <input
                    value={replyMessage}
                    onChange={(e) => setReplyMessage(e.target.value)}
                    placeholder="输入补充信息..."
                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm"
                  />
                  <button
                    onClick={handleReply}
                    className="rounded-lg bg-amber-600 px-3 py-1.5 text-sm text-white hover:bg-amber-700"
                  >
                    回复
                  </button>
                </>
              ) : (
                <button
                  onClick={handleCancel}
                  className="rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50"
                >
                  取消
                </button>
              )}
            </div>
          )}
          {(run.status === 'failed' || run.status === 'timeout') && (
            <button
              onClick={handleRetry}
              className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700"
            >
              重试
            </button>
          )}
        </div>
        {actionError && <p className="mt-3 text-sm text-red-600">{actionError}</p>}

        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <div className="flex justify-between">
            <dt className="text-slate-400">策略</dt>
            <dd className="text-slate-700">{run.strategy}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-400">当前步骤</dt>
            <dd className="text-slate-700">{run.current_step}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-400">成本</dt>
            <dd className="text-slate-700">¥{(run.cost_cents / 100).toFixed(2)}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-400">实时连接</dt>
            <dd className="text-slate-700">
              {connected ? (
                <span className="text-emerald-600">已连接</span>
              ) : (
                <span className="text-slate-400">未连接</span>
              )}
            </dd>
          </div>
        </dl>

        {run.error && (
          <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-600">
            <span className="font-medium">错误：</span>
            {run.error}
          </div>
        )}
        {run.result && (
          <div className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700">
            <span className="font-medium">结果：</span>
            <pre className="mt-1 whitespace-pre-wrap font-mono text-xs">
              {JSON.stringify(run.result, null, 2)}
            </pre>
          </div>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-slate-700">执行步骤</h2>
        <StepList steps={run.steps} />
      </div>

      {run.artifacts.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-slate-700">制品</h2>
          <ul className="space-y-2">
            {run.artifacts.map((art) => (
              <li key={art.id} className="flex items-center justify-between text-sm">
                <span className="text-slate-700">{art.filename}</span>
                <a
                  href={api.artifactDownloadUrl(run.id, art.id)}
                  className="text-blue-600 hover:underline"
                  target="_blank"
                  rel="noreferrer"
                >
                  下载（{(art.size_bytes / 1024).toFixed(1)} KB）
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {logs.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-slate-900 p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-slate-300">运行日志</h2>
          <div className="max-h-64 space-y-1 overflow-y-auto font-mono text-xs">
            {logs.map((log, i) => (
              <p key={i} className="text-slate-400">
                <span className="text-slate-500">[{log.level}]</span> {log.line}
              </p>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
