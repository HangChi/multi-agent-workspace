/** Agent 管理页（T037）：列表/创建/编辑/归档/删除，TanStack Query 缓存。 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
import {
  Agent,
  archiveAgent,
  createAgent,
  deleteAgent,
  fetchAgents,
  updateAgent,
} from '../api/endpoints'
import { AgentForm, AgentFormValues } from '../components/agent/AgentForm'
import { AgentListItem } from '../components/agent/AgentListItem'
import { Button } from '../components/ui/Button'
import { IconTile } from '../components/ui/Card'
import { useConfirm } from '../components/ui/ConfirmDialog'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Icon } from '../components/ui/Icons'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { SegmentedControl } from '../components/ui/Segmented'
import { CardSkeleton } from '../components/ui/Skeleton'
import { useToast } from '../components/ui/Toast'

type Filter = 'all' | 'active' | 'archived'

const FILTER_OPTIONS: { value: Filter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '活跃' },
  { value: 'archived', label: '已归档' },
]

export function AgentPage() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Agent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')

  const { data, isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: () => fetchAgents(),
  })

  const createMut = useMutation({
    mutationFn: createAgent,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['agents'] })
      setShowForm(false)
      setError(null)
      toast.success('Agent 已创建')
    },
    onError: (err) => setError(extractError(err)),
  })

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<AgentFormValues> }) =>
      updateAgent(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['agents'] })
      setEditing(null)
      setError(null)
      toast.success('修改已保存')
    },
    onError: (err) => setError(extractError(err)),
  })

  const archiveMut = useMutation({
    mutationFn: archiveAgent,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['agents'] })
      toast.success('Agent 已归档', '已归档的 Agent 不再出现在分配目标中')
    },
    onError: (err) => setError(extractError(err)),
  })

  const deleteMut = useMutation({
    mutationFn: deleteAgent,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['agents'] })
      toast.success('Agent 已删除')
    },
    onError: (err) => setError(extractError(err)),
  })

  const items = useMemo(() => data?.items ?? [], [data])
  const filtered = useMemo(
    () => (filter === 'all' ? items : items.filter((a) => a.status === filter)),
    [items, filter],
  )

  const activeCount = items.filter((a) => a.status === 'active').length
  const formOpen = showForm || !!editing

  const onSubmit = (values: AgentFormValues) => {
    if (editing) {
      updateMut.mutate({ id: editing.id, payload: values })
    } else {
      createMut.mutate(values)
    }
  }

  const onArchive = async (agent: Agent) => {
    const ok = await confirm({
      title: `归档「${agent.name}」？`,
      description: '归档后该 Agent 不再出现在看板的分配目标中，历史运行记录保留。',
      confirmText: '归档',
      tone: 'warning',
    })
    if (ok) archiveMut.mutate(agent.id)
  }

  const onDelete = async (agent: Agent) => {
    const ok = await confirm({
      title: `删除「${agent.name}」？`,
      description: '该操作不可撤销。若 Agent 仍被某个组引用，后端会拒绝删除并给出提示。',
      confirmText: '删除',
      tone: 'danger',
    })
    if (ok) deleteMut.mutate(agent.id)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditing(null)
  }

  return (
    <Page>
      <PageHeader
        title="Agent 管理"
        description="创建可执行的 Agent：提示词、模型、工具集与成本上限。"
        icon={
          <IconTile tone="brand" size="lg">
            <Icon name="bot" className="h-5 w-5" />
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
              }
              setError(null)
            }}
          >
            {formOpen ? '收起' : '新建 Agent'}
          </Button>
        }
      />

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {formOpen && (
        <div className="rounded-xl border border-line/90 bg-surface shadow-card animate-fade-in-up">
          <div className="flex items-center gap-2.5 border-b border-line-subtle px-5 py-3.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-50 text-accent ring-1 ring-inset ring-brand-100">
              <Icon name={editing ? 'edit' : 'plus'} className="h-4 w-4" />
            </span>
            <h2 className="text-sm font-semibold text-ink">
              {editing ? `编辑 Agent「${editing.name}」` : '新建 Agent'}
            </h2>
          </div>
          <div className="p-5">
            <AgentForm
              initial={editing ?? undefined}
              onSubmit={onSubmit}
              onCancel={closeForm}
              submitting={createMut.isPending || updateMut.isPending}
            />
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <SegmentedControl
          options={FILTER_OPTIONS}
          value={filter}
          onChange={setFilter}
          ariaLabel="按状态筛选 Agent"
        />
        <p className="text-xs text-ink-faint">
          共 <span className="font-medium text-ink-soft">{items.length}</span> 个 · 活跃{' '}
          <span className="font-medium text-emerald-600">{activeCount}</span> 个
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={filter === 'archived' ? 'archive' : 'bot'}
          title={
            items.length === 0
              ? '还没有 Agent'
              : filter === 'archived'
                ? '没有已归档的 Agent'
                : '没有活跃的 Agent'
          }
          description={
            items.length === 0
              ? 'Agent 是可执行的最小单元：给它一段提示词、一个模型和一组工具，就能开始接需求。'
              : '切换筛选条件查看其他状态的 Agent。'
          }
          action={
            items.length === 0 ? (
              <Button variant="primary" leftIcon="plus" onClick={() => setShowForm(true)}>
                新建第一个 Agent
              </Button>
            ) : (
              <Button variant="secondary" leftIcon="refresh" onClick={() => setFilter('all')}>
                查看全部
              </Button>
            )
          }
        />
      ) : (
        <div className="space-y-3">
          {filtered.map((agent) => (
            <AgentListItem
              key={agent.id}
              agent={agent}
              onEdit={() => {
                setEditing(agent)
                setShowForm(true)
              }}
              onArchive={() => void onArchive(agent)}
              onDelete={() => void onDelete(agent)}
            />
          ))}
        </div>
      )}
    </Page>
  )
}
