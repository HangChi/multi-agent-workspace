/** 依赖管理区块（009 T020）：详情弹层里的「前置需求」。
 *
 * 三块内容对应 US1/US2：
 * - 被挡提示（blocked 时）：「有 N 个前置未完成，暂不能分配执行」——
 *   门禁的原因要前置可见，否则用户把 409 当成故障；
 * - 前置列表：标题 + 状态徽章 + 终态标记 + 移除按钮；
 * - 添加器：下拉选需求（默认带出标题），仅列当前用户可见范围内的需求
 *   （scope=relevant：我的 + 派给我的——给自己不相关的需求连边没有意义）。
 *
 * 权限由后端判（owner-or-assignee，403/404）；前端对所有登录 member
 * 都渲染操作入口，失败时走错误横幅。
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../../api/client'
import { addDependency, fetchRequests, removeDependency } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Icon } from '../ui/Icons'
import { Input, Select } from '../ui/Field'
import { useConfirm } from '../ui/useConfirm'
import { useToast } from '../ui/useToast'

const STATUS_TONE: Record<string, 'emerald' | 'rose' | 'slate' | 'sky' | 'amber'> = {
  created: 'slate',
  pending: 'amber',
  accepted: 'sky',
  assigned: 'sky',
  running: 'sky',
  completed: 'emerald',
  failed: 'rose',
  cancelled: 'slate',
}

const STATUS_LABEL: Record<string, string> = {
  created: '待处理',
  pending: '待接收',
  accepted: '已接收',
  assigned: '已分配',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

interface Props {
  requestId: string
  /** 详情端下发的 blocked（避免组件再查一次）。 */
  blocked: boolean
  /**
   * 增删边成功后回调。父级（详情弹层）的 dependencies 多是一次性快照，
   * 不吃 invalidateQueries——不通知父级重拉，弹层会显示陈旧边列表
   * （013 修的就是这个：加边后移除按钮不出现的现场）。
   */
  onChanged?: () => void
  /** 详情端下发的依赖概要（含 terminal，无需再拉列表）。 */
  dependencies: {
    id: string
    request_id: string
    title: string
    status: string
    terminal: boolean
  }[]
}

export function DependencySection({ requestId, blocked, dependencies, onChanged }: Props) {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState('')
  /** 候选搜索：服务端 q（候选可能几百条，本地过滤 page_size=100 会静默漏） */
  const [candidateQ, setCandidateQ] = useState('')
  const [candidateQDebounced, setCandidateQDebounced] = useState('')

  useEffect(() => {
    const timer = window.setTimeout(() => setCandidateQDebounced(candidateQ.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [candidateQ])

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['requests'] })
    void qc.invalidateQueries({ queryKey: ['dependencies', requestId] })
  }

  // 添加器的候选：当前用户可见范围（relevant）的需求，排除已连的
  const { data: candidates } = useQuery({
    queryKey: ['dependencies', requestId, 'candidates', candidateQDebounced],
    queryFn: () =>
      fetchRequests({
        scope: 'relevant',
        page_size: 50,
        q: candidateQDebounced || undefined,
      }),
  })
  const linkedIds = new Set(dependencies.map((d) => d.request_id))
  const options = (candidates?.items ?? []).filter(
    (r) => r.id !== requestId && !linkedIds.has(r.id)
  )

  const addMut = useMutation({
    mutationFn: (dependencyId: string) => addDependency(requestId, dependencyId),
    onSuccess: () => {
      invalidate()
      onChanged?.()
      setPicked('')
      toast.success('已添加前置依赖')
    },
    onError: (err) => setError(extractError(err)),
  })

  const removeMut = useMutation({
    mutationFn: (dependencyId: string) => removeDependency(requestId, dependencyId),
    onSuccess: () => {
      invalidate()
      onChanged?.()
      toast.success('已移除前置依赖')
    },
    onError: (err) => setError(extractError(err)),
  })

  const unmet = dependencies.filter((d) => !d.terminal)

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-ui font-medium text-ink-soft">
          前置需求
          <span className="ml-1 text-xs font-normal text-ink-faint">{dependencies.length} 个</span>
        </p>
      </div>

      {blocked && (
        <p className="flex items-start gap-1.5 rounded-lg bg-amber-50 px-2.5 py-2 text-xs text-amber-700 ring-1 ring-inset ring-amber-200/70 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/25">
          <Icon name="alert-triangle" className="mt-px h-3.5 w-3.5 shrink-0" />有 {unmet.length}{' '}
          个前置未完成，暂不能分配执行；前置全部完成后会收到通知。
        </p>
      )}

      {dependencies.length > 0 && (
        <ul className="space-y-1.5">
          {dependencies.map((dep) => (
            <li
              key={dep.id}
              className="flex items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-2"
            >
              <Icon
                name={dep.terminal ? 'check-circle' : 'clock'}
                className={
                  dep.terminal
                    ? 'h-3.5 w-3.5 shrink-0 text-emerald-500'
                    : 'h-3.5 w-3.5 shrink-0 text-amber-500'
                }
              />
              <span className="min-w-0 flex-1 truncate text-ui text-ink-soft">{dep.title}</span>
              <Badge tone={STATUS_TONE[dep.status] ?? 'slate'}>
                {STATUS_LABEL[dep.status] ?? dep.status}
              </Badge>
              <button
                type="button"
                onClick={async () => {
                  const ok = await confirm({
                    title: '移除前置依赖？',
                    description: `「${dep.title}」将不再作为本需求的前置条件——若它尚未完成，本需求会立即变为可分配。`,
                    tone: 'warning',
                    confirmText: '移除',
                  })
                  if (ok) removeMut.mutate(dep.request_id)
                }}
                aria-label={`移除前置 ${dep.title}`}
                className="shrink-0 rounded-md p-1 text-ink-faint transition-colors hover:bg-surface-subtle hover:text-rose-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40"
              >
                <Icon name="x" className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* 添加器：先搜（服务端 q）再下拉选需求（默认带出标题；排除自己与已连的） */}
      <div className="space-y-2">
        <Input
          value={candidateQ}
          onChange={(e) => setCandidateQ(e.target.value)}
          placeholder="搜索候选需求（标题 / 描述）"
          leftIcon="search"
          controlSize="sm"
          aria-label="搜索候选前置需求"
          wrapperClassName="w-full"
          rightSlot={
            candidateQ ? (
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setCandidateQ('')}
                aria-label="清除候选搜索"
              >
                清除
              </Button>
            ) : undefined
          }
        />
        <div className="flex items-center gap-2">
          <Select
            value={picked}
            onChange={(e) => setPicked(e.target.value)}
            controlSize="sm"
            aria-label="选择前置需求"
            wrapperClassName="flex-1"
          >
            <option value="">
              {candidateQDebounced ? '搜索结果里选择前置需求…' : '添加前置需求…'}
            </option>
            {options.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title}
                {r.blocked ? '（有未完成前置）' : ''}
              </option>
            ))}
          </Select>
          <Button
            variant="secondary"
            size="sm"
            disabled={!picked || addMut.isPending}
            loading={addMut.isPending}
            onClick={() => picked && addMut.mutate(picked)}
          >
            添加
          </Button>
        </div>
        {candidateQDebounced && options.length === 0 && (
          <p className="text-micro text-ink-faint">没有匹配「{candidateQDebounced}」的需求</p>
        )}
      </div>

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}
    </div>
  )
}
