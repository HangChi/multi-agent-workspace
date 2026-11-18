/**
 * 调度规则页（T053）：列表 + 新建/编辑 + 启停 + 删除 + 触发历史。
 *
 * 写权限有两层，且不是同一个判断：
 *
 * - **角色层**：viewer 对所有 `/schedules` 端点都是 403。因此这里直接渲染
 *   403 页面，**不发请求**——明知会失败还发一次，只会让控制台多一条红色噪声，
 *   并让用户多等一个往返才看到"无权访问"。
 * - **归属层**：admin 能读到全部规则但**不可写他人的**（契约 §1 的刻意取舍）。
 *   所以 `canWrite` 是「不是 viewer」且「这条规则是我的」，逐条判断，
 *   不能用一个全局布尔。
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { extractError } from '../api/client'
import {
  createSchedule,
  deleteSchedule,
  fetchAgents,
  fetchGroups,
  fetchSchedules,
  updateSchedule,
} from '../api/endpoints'
import type { Schedule } from '../api/endpoints'
import { ScheduleForm } from '../components/schedule/ScheduleForm'
import type { ScheduleFormValues } from '../components/schedule/ScheduleForm'
import { ScheduleList } from '../components/schedule/ScheduleList'
import { TriggerHistory } from '../components/schedule/TriggerHistory'
import { Button } from '../components/ui/Button'
import { IconTile } from '../components/ui/Card'
import { useConfirm } from '../components/ui/useConfirm'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Icon } from '../components/ui/Icons'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { Pagination } from '../components/ui/Pagination'
import { SegmentedControl } from '../components/ui/Segmented'
import { CardSkeleton } from '../components/ui/Skeleton'
import { useToast } from '../components/ui/useToast'
import { useAuth } from '../hooks/useAuth'

type Filter = 'all' | 'enabled' | 'disabled'

const FILTER_OPTIONS: { value: Filter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'enabled', label: '启用中' },
  { value: 'disabled', label: '已停用' },
]

const PAGE_SIZE = 10

/**
 * 后端把可读错误放在 `error.detail.field` 时，把它提出来交给表单定位。
 *
 * 只有带字段名的错误才应该落到某个输入框；其余（如 403、404、
 * 「配额已用尽」这类整体性失败）走横幅，否则会把红色描边加到无关的输入框上。
 */
function extractField(error: unknown): string | null {
  const detail = (error as { response?: { data?: { error?: { detail?: { field?: unknown } } } } })
    ?.response?.data?.error?.detail
  return typeof detail?.field === 'string' ? detail.field : null
}

export function SchedulesPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const { user } = useAuth()

  const [filter, setFilter] = useState<Filter>('all')
  const [page, setPage] = useState(1)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Schedule | null>(null)
  const [historyFor, setHistoryFor] = useState<Schedule | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [errorField, setErrorField] = useState<string | null>(null)

  const isViewer = user?.role === 'viewer'

  const { data, isLoading } = useQuery({
    queryKey: ['schedules', 'list', filter, page],
    queryFn: () =>
      fetchSchedules({
        enabled: filter === 'all' ? undefined : filter === 'enabled',
        page,
        page_size: PAGE_SIZE,
      }),
    // viewer 发出去也是 403，不如不发
    enabled: !isViewer,
  })

  // 目标下拉：活跃 Agent + 全部组。all: true 避免第 N 页之后的选项静默消失
  const { data: agents } = useQuery({
    queryKey: ['agents', 'list', 'active', 'all'],
    queryFn: () => fetchAgents({ status: 'active', all: true }),
    enabled: !isViewer,
  })
  const { data: groups } = useQuery({
    queryKey: ['groups', 'all'],
    queryFn: () => fetchGroups({ all: true }),
    enabled: !isViewer,
  })

  const clearError = () => {
    setError(null)
    setErrorField(null)
  }

  const fail = (err: unknown) => {
    setError(extractError(err))
    setErrorField(extractField(err))
  }

  const createMut = useMutation({
    mutationFn: createSchedule,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['schedules'] })
      setShowForm(false)
      clearError()
      toast.success('规则已创建', '启用后每个计划时刻都会自动建单并分配')
    },
    onError: fail,
  })

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<ScheduleFormValues> }) =>
      // 只送用户真正改过的字段由表单负责；这里直接透传 payload
      updateSchedule(id, payload as Parameters<typeof updateSchedule>[1]),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['schedules'] })
      setEditing(null)
      clearError()
      toast.success('修改已保存')
    },
    onError: fail,
  })

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateSchedule(id, { enabled }),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: ['schedules'] })
      toast.success(
        vars.enabled ? '规则已启用' : '规则已停用',
        vars.enabled ? '下次触发时间已重新计算' : '停用期间不会产生任何记录'
      )
    },
    onError: fail,
  })

  const deleteMut = useMutation({
    mutationFn: deleteSchedule,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['schedules'] })
      toast.success('规则已删除', '已由它创建的需求与运行记录全部保留')
    },
    onError: fail,
  })

  const items = useMemo(() => data?.items ?? [], [data])
  const total = data?.total ?? 0
  const formOpen = showForm || !!editing

  const changeFilter = (next: Filter) => {
    setFilter(next)
    setPage(1)
  }

  /** 写权限按条判断：admin 能看别人的，但不能改。 */
  const canWrite = (schedule: Schedule) => !isViewer && schedule.owner_id === user?.id

  const onSubmit = (values: ScheduleFormValues) => {
    if (editing) {
      updateMut.mutate({ id: editing.id, payload: values })
    } else {
      createMut.mutate({
        name: values.name,
        description: values.description,
        cron_expr: values.cron_expr,
        timezone: values.timezone,
        template: values.template,
        target: values.target,
        enabled: values.enabled,
      })
    }
  }

  const onDelete = async (schedule: Schedule) => {
    const ok = await confirm({
      title: `删除规则「${schedule.name}」？`,
      description:
        '该规则不再产生新的需求。已经由它创建的需求、Run 与触发历史会保留，但需求上的来源信息会清空。此操作不可撤销。',
      confirmText: '删除',
      tone: 'danger',
    })
    if (ok) deleteMut.mutate(schedule.id)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditing(null)
    clearError()
  }

  // viewer：直接给 403，不发那个注定 403 的请求
  if (isViewer) {
    return (
      <Page width="narrow">
        <div className="rounded-2xl border border-line bg-surface p-6 text-center shadow-card">
          <span className="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-rose-50 text-rose-500 ring-1 ring-inset ring-rose-100">
            <Icon name="lock" className="h-5 w-5" />
          </span>
          <p className="text-base font-semibold text-ink">403 · 无访问权限</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-muted">
            定时调度仅对成员与管理员开放。只读角色无法查看或管理任何调度规则。
          </p>
          <Link
            to="/"
            className="mt-4 inline-flex h-9 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 text-sm font-medium text-ink-soft shadow-card transition-colors hover:bg-surface-muted"
          >
            <Icon name="arrow-left" className="h-4 w-4" />
            返回看板
          </Link>
        </div>
      </Page>
    )
  }

  return (
    <Page>
      <PageHeader
        eyebrow="设置"
        title="定时调度"
        description="按周期自动创建需求并分配执行。错过的时间点会记录为「已错过」，不会补跑。"
        icon={
          <IconTile tone="brand" size="lg">
            <Icon name="clock" className="h-5 w-5" />
          </IconTile>
        }
        actions={
          <Button
            variant={formOpen ? 'secondary' : 'primary'}
            leftIcon={formOpen ? 'chevron-up' : 'plus'}
            onClick={() => {
              if (formOpen) {
                closeForm()
              } else {
                setEditing(null)
                setShowForm(true)
                clearError()
              }
            }}
          >
            {formOpen ? '收起' : '新建规则'}
          </Button>
        }
      />

      {error && !formOpen && <ErrorBanner error={error} onDismiss={clearError} />}

      {formOpen && (
        <div className="rounded-xl border border-line/90 bg-surface shadow-card animate-fade-in-up">
          <div className="flex items-center gap-2.5 border-b border-line-subtle px-5 py-3.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-50 text-accent ring-1 ring-inset ring-brand-100">
              <Icon name={editing ? 'edit' : 'plus'} className="h-4 w-4" />
            </span>
            <h2 className="text-sm font-semibold text-ink">
              {editing ? `编辑规则「${editing.name}」` : '新建调度规则'}
            </h2>
            {editing && (
              <span className="ml-auto text-micro text-ink-faint">
                修改周期或时区会重算下次触发时间
              </span>
            )}
          </div>
          <div className="p-5">
            <ScheduleForm
              initial={editing}
              agents={agents?.items ?? []}
              groups={groups?.items ?? []}
              onSubmit={onSubmit}
              onCancel={closeForm}
              submitting={createMut.isPending || updateMut.isPending}
              error={error}
              errorField={errorField}
            />
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <SegmentedControl
          options={FILTER_OPTIONS}
          value={filter}
          onChange={changeFilter}
          ariaLabel="按启用状态筛选规则"
        />
        <p className="text-xs text-ink-faint">
          共 <span className="font-medium text-ink-soft">{total}</span> 条
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon="clock"
          title={
            total === 0 && filter === 'all'
              ? '还没有调度规则'
              : filter === 'enabled'
                ? '没有启用中的规则'
                : '没有已停用的规则'
          }
          description={
            total === 0 && filter === 'all'
              ? '写一条周期表达式（如工作日 09:00），系统就会在到点时自动建需求并交给 Agent 执行。'
              : '切换筛选条件查看其他状态的规则。'
          }
          action={
            total === 0 && filter === 'all' ? (
              <Button variant="primary" leftIcon="plus" onClick={() => setShowForm(true)}>
                新建第一条规则
              </Button>
            ) : (
              <Button variant="secondary" leftIcon="refresh" onClick={() => changeFilter('all')}>
                查看全部
              </Button>
            )
          }
        />
      ) : (
        <div className="space-y-3">
          {items.map((schedule) => (
            <ScheduleList
              key={schedule.id}
              schedule={schedule}
              canWrite={canWrite(schedule)}
              toggling={toggleMut.isPending && toggleMut.variables?.id === schedule.id}
              onToggleEnabled={(target, enabled) => toggleMut.mutate({ id: target.id, enabled })}
              onEdit={(target) => {
                setEditing(target)
                setShowForm(true)
                clearError()
              }}
              onDelete={(target) => void onDelete(target)}
              onShowHistory={setHistoryFor}
            />
          ))}
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onChange={setPage}
            loading={isLoading}
          />
        </div>
      )}

      {historyFor && (
        <TriggerHistory
          open
          onClose={() => setHistoryFor(null)}
          scheduleId={historyFor.id}
          scheduleName={historyFor.name}
        />
      )}
    </Page>
  )
}
