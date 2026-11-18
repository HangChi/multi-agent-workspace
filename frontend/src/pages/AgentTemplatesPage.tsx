/**
 * Agent 模板管理页（T031）。
 *
 * 写权限分两层，与 SchedulesPage 同一套思路但判据不同：
 *
 * - **角色层**：viewer 对所有模板端点都是 403（模板含系统提示词，属配置资产），
 *   因此直接渲染 403 页面，不发请求。
 * - **内置层**：内置模板（`owner_id` 为空，种子脚本注入）对所有成员只读，
 *   连编辑/删除入口都不渲染——给了入口也是 403，只会多一次红色控制台噪声。
 *
 * 「用这个模板创建」走 `TemplatePicker` 之外的直接路径：这里已经知道是哪个模板，
 * 直接打开预填好的 AgentForm，不让用户再选一次。
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { extractError } from '../api/client'
import {
  createAgent,
  deleteAgentTemplate,
  fetchAgentTemplates,
  updateAgentTemplate,
} from '../api/endpoints'
import type { Agent, AgentTemplate } from '../api/endpoints'
import { AgentForm } from '../components/agent/AgentForm'
import type { AgentFormValues } from '../components/agent/AgentForm'
import { TemplateForm } from '../components/agent/TemplateForm'
import type { TemplateFormValues } from '../components/agent/TemplateForm'
import { Badge } from '../components/ui/Badge'
import { Button, IconButton } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { useConfirm } from '../components/ui/useConfirm'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { IconTile } from '../components/ui/Card'
import { Icon } from '../components/ui/Icons'
import { Modal } from '../components/ui/Modal'
import { Input } from '../components/ui/Field'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { CardSkeleton } from '../components/ui/Skeleton'
import { useToast } from '../components/ui/useToast'
import { useAuth } from '../hooks/useAuth'

export function AgentTemplatesPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const navigate = useNavigate()
  const { user } = useAuth()

  const [editing, setEditing] = useState<AgentTemplate | null>(null)
  /** 模板搜索：模板数量会随团队沉淀增长，滚列表不如先搜 */
  const [q, setQ] = useState('')
  const [using, setUsing] = useState<AgentTemplate | null>(null)
  const [error, setError] = useState<string | null>(null)

  const canManage = user?.role === 'admin' || user?.role === 'member'

  const { data, isLoading } = useQuery({
    queryKey: ['agent-templates', 'page'],
    queryFn: () => fetchAgentTemplates({ scope: 'all', page_size: 100 }),
    enabled: canManage,
  })

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['agent-templates'] })
  }

  const updateMutation = useMutation({
    mutationFn: (values: TemplateFormValues) => updateAgentTemplate(editing!.id, values),
    onSuccess: () => {
      toast.success('模板已保存')
      setEditing(null)
      setError(null)
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAgentTemplate(id),
    onSuccess: () => {
      toast.success('模板已删除')
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  const useMutationCreate = useMutation({
    mutationFn: (values: AgentFormValues) =>
      createAgent({
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
    onSuccess: (agent: Agent) => {
      toast.success('Agent 已创建')
      setUsing(null)
      invalidate()
      void navigate(`/agents?highlight=${agent.id}`)
    },
    onError: (err) => setError(extractError(err)),
  })

  if (!canManage) {
    return (
      <Page>
        <div className="mx-auto max-w-md py-16 text-center">
          <IconTile tone="rose" size="lg" className="mx-auto mb-4">
            <Icon name="shield" className="h-6 w-6" />
          </IconTile>
          <h1 className="text-lg font-semibold text-ink">403 · 无访问权限</h1>
          <p className="mt-2 text-sm text-ink-muted">
            模板包含系统提示词等配置资产，仅成员与管理员可查看。
          </p>
        </div>
      </Page>
    )
  }

  const items = data?.items ?? []

  const handleDelete = async (template: AgentTemplate) => {
    const ok = await confirm({
      title: '删除模板',
      description: `删除「${template.name}」后无法恢复。已由它创建的 Agent 不受影响。`,
      confirmText: '删除',
      tone: 'danger',
    })
    if (ok) deleteMutation.mutate(template.id)
  }

  const keyword = q.trim().toLowerCase()
  const shown = keyword
    ? items.filter(
        (t) =>
          t.name.toLowerCase().includes(keyword) ||
          (t.description ?? '').toLowerCase().includes(keyword)
      )
    : items
  const builtins = shown.filter((t) => t.is_builtin)
  const mine = shown.filter((t) => !t.is_builtin)

  return (
    <Page>
      <PageHeader
        eyebrow="设置"
        title="Agent 模板"
        description="把调好的配置沉淀成模板，创建 Agent 时一键复用。模板是快照——改模板不会改动已创建的 Agent。"
        icon={
          <IconTile tone="brand" size="lg">
            <Icon name="layers" className="h-5 w-5" />
          </IconTile>
        }
      />

      {error && (
        <div className="mb-4">
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {isLoading && (
        <div className="space-y-2">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      )}

      {!isLoading && shown.length === 0 && (
        <EmptyState
          icon={keyword ? 'search' : 'layers'}
          title={keyword ? `没有匹配「${q.trim()}」的模板` : '还没有模板'}
          description={
            keyword
              ? '换个关键词，或清空搜索看看全部模板。'
              : '内置模板会在部署时由种子脚本注入；你也可以在 Agent 列表里把调好的 Agent「另存为模板」。'
          }
          action={
            keyword ? (
              <Button variant="secondary" leftIcon="refresh" onClick={() => setQ('')}>
                清空搜索
              </Button>
            ) : undefined
          }
        />
      )}

      <div className="flex flex-col gap-2.5 lg:flex-row lg:items-center lg:justify-between">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索模板名称或描述"
          leftIcon="search"
          controlSize="sm"
          aria-label="搜索模板"
          wrapperClassName="w-full lg:w-72"
          rightSlot={
            q.trim() ? (
              <IconButton
                icon="x"
                label="清除搜索"
                size="xs"
                onClick={() => setQ('')}
                className="mr-0.5"
              />
            ) : undefined
          }
        />
        <p className="text-2xs text-ink-faint">
          {q.trim() ? (
            <>
              「<span className="text-ink-soft">{q.trim()}</span>」命中{' '}
              <span className="font-medium text-ink-soft">{items.length}</span> 个
            </>
          ) : (
            <>
              共 <span className="font-medium text-ink-soft">{items.length}</span> 个模板
            </>
          )}
        </p>
      </div>

      {builtins.length > 0 && (
        <section className="mb-8">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-[13px] font-medium text-ink-soft">
              内置模板
              <span className="ml-1.5 text-2xs font-normal text-ink-faint">
                {builtins.length} 个 · 只读
              </span>
            </h2>
            <p className="text-2xs text-ink-faint">
              内置模板不可修改：需要改就先「用这个模板」创建 Agent，再另存为自己的模板。
            </p>
          </div>
          <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {builtins.map((t) => (
              <TemplateCard
                key={t.id}
                template={t}
                onUse={() => setUsing(t)}
                onEdit={null}
                onDelete={null}
              />
            ))}
          </ul>
        </section>
      )}

      {mine.length > 0 && (
        <section>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="text-[13px] font-medium text-ink-soft">
              我的模板
              <span className="ml-1.5 text-2xs font-normal text-ink-faint">
                {mine.length} 个 · 可编辑
              </span>
            </h2>
          </div>
          <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {mine.map((t) => (
              <TemplateCard
                key={t.id}
                template={t}
                onUse={() => setUsing(t)}
                onEdit={() => setEditing(t)}
                onDelete={() => void handleDelete(t)}
              />
            ))}
          </ul>
        </section>
      )}

      {editing && (
        <Modal
          open
          onClose={() => setEditing(null)}
          title={`编辑模板：${editing.name}`}
          description="保存后只影响以后用该模板创建的 Agent，已创建的不变。"
          size="2xl"
        >
          <TemplateForm
            initial={{
              name: editing.name,
              description: editing.description,
              system_prompt: editing.system_prompt,
              model: editing.model,
              temperature: editing.temperature,
              max_steps: editing.max_steps,
              max_run_seconds: editing.max_run_seconds,
              tools: editing.tools,
              cost_limit_cents: editing.cost_limit_cents,
              egress_mode: editing.egress_mode,
            }}
            submitting={updateMutation.isPending}
            onCancel={() => setEditing(null)}
            onSubmit={(values) => updateMutation.mutate(values)}
          />
        </Modal>
      )}

      {using && (
        <Modal
          open
          onClose={() => setUsing(null)}
          title={`用模板创建：${using.name}`}
          description="表单已按模板预填，可改任何字段后再创建。"
          size="2xl"
        >
          <AgentForm
            initial={{
              id: '',
              owner_id: user?.id ?? '',
              name: `${using.name}`,
              description: using.description,
              system_prompt: using.system_prompt,
              model: using.model,
              temperature: using.temperature,
              max_steps: using.max_steps,
              max_run_seconds: using.max_run_seconds,
              tools: using.tools,
              cost_limit_cents: using.cost_limit_cents,
              egress_mode: using.egress_mode,
              status: 'active',
              created_at: '',
              updated_at: '',
            }}
            submitting={useMutationCreate.isPending}
            onCancel={() => setUsing(null)}
            onSubmit={(values) => useMutationCreate.mutate(values)}
          />
        </Modal>
      )}
    </Page>
  )
}

function TemplateCard({
  template,
  onUse,
  onEdit,
  onDelete,
}: {
  template: AgentTemplate
  onUse: () => void
  onEdit: (() => void) | null
  onDelete: (() => void) | null
}) {
  return (
    <Card padding="none" className="overflow-hidden">
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-[15px] font-semibold text-ink">{template.name}</h3>
            {template.is_builtin && <Badge tone="slate">内置</Badge>}
          </div>
          <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-ink-muted">
            {template.description || '暂无描述'}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button size="sm" variant="primary" leftIcon="plus" onClick={onUse}>
            用这个模板
          </Button>
          {onEdit && (
            <Button size="sm" variant="secondary" leftIcon="edit" onClick={onEdit}>
              编辑
            </Button>
          )}
          {onDelete && (
            <Button
              size="sm"
              variant="ghost"
              leftIcon="trash"
              onClick={onDelete}
              className="text-rose-600 hover:bg-rose-50 hover:text-rose-700 dark:text-rose-300 dark:hover:bg-rose-500/15"
            >
              删除
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line-subtle bg-surface-muted/60 px-4 py-2.5">
        <span className="inline-flex items-center gap-1.5 rounded-md bg-surface px-1.5 py-0.5 font-mono text-2xs text-ink-soft ring-1 ring-inset ring-line">
          {template.model}
        </span>
        <span className="inline-flex items-center gap-1.5 text-2xs text-ink-muted">
          <Icon name="layers" className="h-3.5 w-3.5 text-ink-faint" />
          {template.tools.length > 0 ? template.tools.join(' · ') : '无工具'}
        </span>
        <span className="text-2xs text-ink-muted">
          上限 ¥{(template.cost_limit_cents / 100).toFixed(2)}
        </span>
        <span className="ml-auto hidden text-2xs text-ink-faint sm:block">
          温度 {template.temperature.toFixed(1)} · 最多 {template.max_steps} 步 · 超时{' '}
          {template.max_run_seconds}s
        </span>
      </div>
    </Card>
  )
}
