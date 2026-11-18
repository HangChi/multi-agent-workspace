/** Agent 管理页（T037）：列表/创建/编辑/归档/删除，TanStack Query 缓存。 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
import {
  Agent,
  archiveAgent,
  cloneAgent,
  createAgent,
  createAgentFromTemplate,
  deleteAgent,
  fetchAgents,
  saveAgentAsTemplate,
  updateAgent,
} from '../api/endpoints'
import type { AgentSaveResponse, AgentTemplate } from '../api/endpoints'
import { AgentForm, AgentFormValues } from '../components/agent/AgentForm'
import { TemplatePicker } from '../components/agent/TemplatePicker'
import { AgentListItem } from '../components/agent/AgentListItem'
import { Button, IconButton } from '../components/ui/Button'
import { useConfirm } from '../components/ui/useConfirm'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Icon } from '../components/ui/Icons'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { Modal } from '../components/ui/Modal'
import { Input } from '../components/ui/Field'
import { Pagination } from '../components/ui/Pagination'
import { SegmentedControl } from '../components/ui/Segmented'
import { CardSkeleton } from '../components/ui/Skeleton'
import { useToast } from '../components/ui/useToast'

type Filter = 'all' | 'active' | 'archived'

const FILTER_OPTIONS: { value: Filter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '活跃' },
  { value: 'archived', label: '已归档' },
]

/** 每页条数。与后端 page_size 上限（100）留出余量，便于列表滚动浏览。 */
const PAGE_SIZE = 12

export function AgentPage() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Agent | null>(null)
  /** 从模板创建：picker 选中的模板；非空时打开预填表单（T032）。 */
  const [fromTemplate, setFromTemplate] = useState<AgentTemplate | null>(null)
  const [savingAs, setSavingAs] = useState<Agent | null>(null)
  const [saveAsName, setSaveAsName] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [page, setPage] = useState(1)
  /** 名称/描述搜索（后端 q 参数，SQL 层完成，不分页失真）。300ms 防抖：
   *  每键一发请求会把后端和分页状态一起打抖。 */
  const [q, setQ] = useState('')
  const [qDebounced, setQDebounced] = useState('')
  useEffect(() => {
    const timer = window.setTimeout(() => setQDebounced(q.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [q])
  const search = qDebounced
  const formWrapRef = useRef<HTMLDivElement>(null)

  // 分页交给后端：status 过滤 + 分页在 SQL 层完成，
  // 前端不再「取全量再本地 slice」—— 那样数据一多首屏就会拖慢。
  const {
    data,
    isLoading,
    isError: listError,
    refetch: refetchAgents,
  } = useQuery({
    queryKey: ['agents', 'list', filter, page, search],
    queryFn: () =>
      fetchAgents({
        status: filter === 'all' ? undefined : filter,
        q: search || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
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

  const cloneMut = useMutation({
    mutationFn: cloneAgent,
    onSuccess: (agent: AgentSaveResponse) => {
      void qc.invalidateQueries({ queryKey: ['agents'] })
      toast.success(`已克隆为「${agent.name}」`, '克隆只复制配置，不带历史运行与授权')
    },
    onError: (err) => setError(extractError(err)),
  })

  const fromTemplateMut = useMutation({
    mutationFn: ({ template, values }: { template: AgentTemplate; values: AgentFormValues }) =>
      createAgentFromTemplate(template.id, {
        name: values.name,
        description: values.description,
        system_prompt: values.system_prompt,
        model: values.model,
        temperature: values.temperature,
        max_steps: values.max_steps,
        max_run_seconds: values.max_run_seconds,
        tools: values.tools,
        cost_limit_cents: values.cost_limit_cents,
        egress_mode: values.egress_mode,
      }),
    onSuccess: (agent: AgentSaveResponse) => {
      void qc.invalidateQueries({ queryKey: ['agents'] })
      setFromTemplate(null)
      setError(null)
      // warning 不阻塞创建，但必须让用户看见（FR-008：不过静默丢弃）
      toast.success(agent.warning ? `已创建，但${agent.warning}` : `已按模板创建「${agent.name}」`)
    },
    onError: (err) => setError(extractError(err)),
  })

  const saveAsMut = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => saveAgentAsTemplate(id, { name }),
    onSuccess: (tpl: AgentTemplate) => {
      void qc.invalidateQueries({ queryKey: ['agent-templates'] })
      setSavingAs(null)
      setSaveAsName('')
      toast.success(`已保存为模板「${tpl.name}」`, '之后改这个 Agent 不会影响模板')
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

  // status 过滤已由后端完成，这里不再本地 filter（否则分页数与总数会对不上）
  const items = useMemo(() => data?.items ?? [], [data])
  const total = data?.total ?? 0
  const formOpen = showForm || !!editing || !!fromTemplate

  /** 切换状态筛选要回到第 1 页，否则可能停在新列表不存在的页码上。 */
  const changeFilter = (next: Filter) => {
    setFilter(next)
    setPage(1)
  }

  /** 搜索变化同样回到第 1 页：否则可能停在结果集里不存在的页码上。 */
  const changeSearch = (next: string) => {
    setQ(next)
    setPage(1)
  }

  const onSubmit = (values: AgentFormValues) => {
    if (editing) {
      updateMut.mutate({ id: editing.id, payload: values })
    } else {
      createMut.mutate(values)
    }
  }

  /**
   * 打开表单的三态互斥：新建 / 编辑 / 按模板创建。
   *
   * 此前是三个独立 state（showForm/editing/fromTemplate），「从模板创建」
   * 打开后不清 fromTemplate 就去点编辑，onSubmit 仍走
   * createAgentFromTemplate——用户以为在改 Agent，实际静默新建了一个。
   * 现在任何入口都把另外两个清干净。
   */
  const openCreate = () => {
    setEditing(null)
    setFromTemplate(null)
    setShowForm(true)
    setError(null)
  }
  const openEdit = (agent: Agent) => {
    setFromTemplate(null)
    setShowForm(false)
    setEditing(agent)
    setError(null)
    // 表单在列表上方，打开后滚到可见区并聚焦首个字段
    requestAnimationFrame(() => {
      formWrapRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
      document.getElementById('agent-form-name')?.focus()
    })
  }
  const openFromTemplate = (tpl: AgentTemplate) => {
    setEditing(null)
    setShowForm(false)
    setFromTemplate(tpl)
    setError(null)
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

  const onClone = (agent: Agent) => {
    cloneMut.mutate(agent.id)
  }

  const onSaveAsTemplate = (agent: Agent) => {
    setSavingAs(agent)
    setSaveAsName(agent.name)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditing(null)
    setFromTemplate(null)
  }

  /** 从模板创建的提交：走 from-template 端点，而不是普通 create。 */
  const onSubmitFromTemplate = (values: AgentFormValues) => {
    if (fromTemplate) fromTemplateMut.mutate({ template: fromTemplate, values })
  }

  return (
    <Page>
      <PageHeader
        eyebrow="工作台"
        title="Agent 管理"
        description="创建可执行的 Agent：提示词、模型、工具集与成本上限。"
        icon={<Icon name="bot" />}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              leftIcon="layers"
              onClick={() => {
                closeForm()
                setPickerOpen(true)
                setError(null)
              }}
            >
              从模板创建
            </Button>
            {formOpen && (
              <Button variant="secondary" leftIcon="chevron-up" onClick={closeForm}>
                收起
              </Button>
            )}
            {/* 主按钮恒为「新建 Agent」：编辑中也要能直接开新的，
                不必先收起（此前主按钮会变成「收起」） */}
            <Button variant="primary" leftIcon="plus" onClick={openCreate}>
              新建 Agent
            </Button>
          </div>
        }
      />

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {/* 列表拉取失败 ≠ 没有 Agent */}
      {listError && (
        <ErrorBanner
          error="Agent 列表加载失败，可能是网络或服务暂时不可用"
          retry={() => void refetchAgents()}
        />
      )}

      {formOpen && (
        <div
          ref={formWrapRef}
          className="rounded-xl border border-line/90 bg-surface shadow-card animate-fade-in-up"
        >
          <div className="flex items-center gap-2.5 border-b border-line-subtle px-5 py-3.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-50 text-accent ring-1 ring-inset ring-brand-100">
              <Icon name={editing ? 'edit' : 'plus'} className="h-4 w-4" />
            </span>
            <h2 className="text-sm font-semibold text-ink">
              {editing
                ? `编辑 Agent「${editing.name}」`
                : fromTemplate
                  ? `按模板创建：${fromTemplate.name}`
                  : '新建 Agent'}
            </h2>
          </div>
          <div className="p-5">
            <AgentForm
              // key 按编辑对象走：连点两个「编辑」不会把 A 的字段留给 B
              // （组件已挂载，useState 初始化器不会重跑——静默数据覆盖）
              key={editing?.id ?? fromTemplate?.id ?? 'new'}
              initial={
                editing ??
                (fromTemplate
                  ? {
                      id: '',
                      owner_id: '',
                      name: fromTemplate.name,
                      description: fromTemplate.description,
                      system_prompt: fromTemplate.system_prompt,
                      model: fromTemplate.model,
                      temperature: fromTemplate.temperature,
                      max_steps: fromTemplate.max_steps,
                      max_run_seconds: fromTemplate.max_run_seconds,
                      tools: fromTemplate.tools,
                      cost_limit_cents: fromTemplate.cost_limit_cents,
                      egress_mode: fromTemplate.egress_mode,
                      status: 'active',
                      created_at: '',
                      updated_at: '',
                    }
                  : undefined)
              }
              onSubmit={fromTemplate ? onSubmitFromTemplate : onSubmit}
              onCancel={closeForm}
              submitting={createMut.isPending || updateMut.isPending || fromTemplateMut.isPending}
              error={error}
            />
          </div>
        </div>
      )}

      <div className="flex flex-col gap-2.5 lg:flex-row lg:items-center lg:justify-between">
        <Input
          value={q}
          onChange={(e) => changeSearch(e.target.value)}
          placeholder="搜索名称或描述"
          leftIcon="search"
          controlSize="sm"
          aria-label="搜索 Agent"
          wrapperClassName="w-full lg:w-72"
          rightSlot={
            search ? (
              <IconButton
                icon="x"
                label="清除搜索"
                size="xs"
                onClick={() => changeSearch('')}
                className="mr-0.5"
              />
            ) : undefined
          }
        />
        <div className="flex flex-wrap items-center gap-3">
          <SegmentedControl
            options={FILTER_OPTIONS}
            value={filter}
            onChange={changeFilter}
            ariaLabel="按状态筛选 Agent"
          />
          <p className="text-xs text-ink-faint">
            {search && (
              <>
                「<span className="text-ink-soft">{search}</span>」命中{' '}
                <span className="font-medium text-ink-soft">{total}</span> 个
              </>
            )}
            {!search && (
              <>
                共 <span className="font-medium text-ink-soft">{total}</span> 个
              </>
            )}
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={search ? 'search' : filter === 'archived' ? 'archive' : 'bot'}
          title={
            search
              ? `没有匹配「${search}」的 Agent`
              : total === 0 && filter === 'all'
                ? '还没有 Agent'
                : filter === 'archived'
                  ? '没有已归档的 Agent'
                  : filter === 'active'
                    ? '没有活跃的 Agent'
                    : '没有 Agent'
          }
          description={
            search
              ? '换个关键词，或清空搜索看看全部 Agent。'
              : total === 0 && filter === 'all'
                ? 'Agent 是可执行的最小单元：给它一段提示词、一个模型和一组工具，就能开始接需求。'
                : '切换筛选条件查看其他状态的 Agent。'
          }
          action={
            search ? (
              <Button variant="secondary" leftIcon="refresh" onClick={() => changeSearch('')}>
                清空搜索
              </Button>
            ) : total === 0 && filter === 'all' ? (
              <Button variant="primary" leftIcon="plus" onClick={() => setShowForm(true)}>
                新建第一个 Agent
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
          {items.map((agent) => (
            <AgentListItem
              key={agent.id}
              agent={agent}
              onEdit={() => openEdit(agent)}
              onArchive={() => void onArchive(agent)}
              onDelete={() => void onDelete(agent)}
              onClone={() => onClone(agent)}
              onSaveAsTemplate={() => onSaveAsTemplate(agent)}
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

      <TemplatePicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onPick={(tpl) => {
          setPickerOpen(false)
          openFromTemplate(tpl)
        }}
      />

      <Modal
        open={!!savingAs}
        onClose={() => {
          setSavingAs(null)
          setSaveAsName('')
        }}
        title="另存为模板"
        description="把当前配置快照成模板。之后修改这个 Agent 不会影响模板，反之亦然。"
      >
        <div className="space-y-4">
          <Input
            label="模板名称"
            required
            value={saveAsName}
            onChange={(e) => setSaveAsName(e.target.value)}
            maxLength={60}
            hint="同名模板会被拒绝，不会自动加后缀"
          />
          <div className="flex items-center justify-end gap-2 border-t border-line-subtle pt-4">
            <Button
              variant="secondary"
              onClick={() => {
                setSavingAs(null)
                setSaveAsName('')
              }}
            >
              取消
            </Button>
            <Button
              variant="primary"
              loading={saveAsMut.isPending}
              disabled={!saveAsName.trim()}
              onClick={() => {
                if (savingAs) {
                  saveAsMut.mutate({ id: savingAs.id, name: saveAsName.trim() })
                }
              }}
            >
              保存模板
            </Button>
          </div>
        </div>
      </Modal>
    </Page>
  )
}
