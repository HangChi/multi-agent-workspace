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

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { extractError } from '../api/client'
import {
  createAgentFromTemplate,
  deleteAgentTemplate,
  exportAgentTemplate,
  fetchAgentTemplates,
  fetchTemplateVersions,
  importAgentTemplate,
  restoreTemplateVersion,
  updateAgentTemplate,
} from '../api/endpoints'
import type { AgentTemplate, TemplateExportPayload, TemplateVersion } from '../api/endpoints'
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
  /** 模板搜索：服务端 q + 300ms 防抖。此前 page_size=100 后本地过滤，
   *  超过 100 条会静默漏结果（与 TemplatePicker 的服务端搜索不一致）。 */
  const [q, setQ] = useState('')
  const [qDebounced, setQDebounced] = useState('')
  useEffect(() => {
    const timer = window.setTimeout(() => setQDebounced(q.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [q])
  const [using, setUsing] = useState<AgentTemplate | null>(null)
  // 023：历史 / 共享 / 导出 合并到一个弹层
  const [inspecting, setInspecting] = useState<AgentTemplate | null>(null)
  const [error, setError] = useState<string | null>(null)

  const canManage = user?.role === 'admin' || user?.role === 'member'

  const {
    data,
    isLoading,
    isError: listError,
    refetch: refetchTemplates,
  } = useQuery({
    queryKey: ['agent-templates', 'page', qDebounced],
    queryFn: () =>
      fetchAgentTemplates({
        scope: 'all',
        q: qDebounced || undefined,
        page_size: 100,
      }),
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

  /**
   * 「用这个模板创建」走 from-template 端点（与 AgentPage 的 TemplatePicker
   * 同一条路）：该端点会把模板里已下线的工具自动过滤并给 warning
   * （FR-008）。此前这里走普通 createAgent，两条「用模板创建」入口
   * 行为不一致，模板页这条丢失了过滤与提示。
   */
  const useMutationCreate = useMutation({
    mutationFn: (values: AgentFormValues) =>
      createAgentFromTemplate(using!.id, {
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
    onSuccess: (agent) => {
      toast.success(agent.warning ? `已创建，但${agent.warning}` : `已按模板创建「${agent.name}」`)
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
  const total = data?.total ?? 0

  const handleDelete = async (template: AgentTemplate) => {
    const ok = await confirm({
      title: '删除模板',
      description: `删除「${template.name}」后无法恢复。已由它创建的 Agent 不受影响。`,
      confirmText: '删除',
      tone: 'danger',
    })
    if (ok) deleteMutation.mutate(template.id)
  }

  const builtins = items.filter((t) => t.is_builtin)
  const mine = items.filter((t) => !t.is_builtin && t.owner_id === user?.id)
  // 023：别人设为 team 的（内置由 builtins 单列，自己的由 mine 单列）
  const teamShared = items.filter(
    (t) => !t.is_builtin && t.visibility === 'team' && t.owner_id !== user?.id
  )

  return (
    <Page>
      <PageHeader
        eyebrow="设置"
        title="Agent 模板"
        description="把调好的配置沉淀成模板，创建 Agent 时一键复用。模板是快照——改模板不会改动已创建的 Agent。"
        icon={<Icon name="layers" />}
      />

      {error && (
        <div className="mb-4">
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {listError && (
        <div className="mb-4">
          <ErrorBanner
            error="模板列表加载失败，可能是网络或服务暂时不可用"
            retry={() => void refetchTemplates()}
          />
        </div>
      )}

      {isLoading && (
        // 骨架与真实布局同构（lg:grid-cols-2），切换不闪
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      )}

      {!isLoading && items.length === 0 && (
        <EmptyState
          icon={qDebounced ? 'search' : 'layers'}
          title={qDebounced ? `没有匹配「${q.trim()}」的模板` : '还没有模板'}
          description={
            qDebounced
              ? '换个关键词，或清空搜索看看全部模板。'
              : '内置模板会在部署时由种子脚本注入；你也可以在 Agent 列表里把调好的 Agent「另存为模板」。'
          }
          action={
            qDebounced ? (
              <Button variant="secondary" leftIcon="refresh" onClick={() => setQ('')}>
                清空搜索
              </Button>
            ) : undefined
          }
        />
      )}

      {/* 搜索栏在空态上方（原来空态渲染在搜索栏上面，顺序颠倒） */}
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
        <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink-soft hover:bg-surface-subtle">
          <Icon name="plus" className="h-3.5 w-3.5" />
          导入模板
          <input
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (!file) return
              void (async () => {
                try {
                  const text = await file.text()
                  const payload = JSON.parse(text) as TemplateExportPayload
                  await importAgentTemplate(payload)
                  toast.success('已导入为你的私有模板')
                  invalidate()
                } catch (err) {
                  setError(extractError(err))
                }
              })()
            }}
          />
        </label>
        <p className="text-micro text-ink-faint">
          {qDebounced ? (
            <>
              「<span className="text-ink-soft">{qDebounced}</span>」命中{' '}
              <span className="font-medium text-ink-soft">{total}</span> 个
            </>
          ) : (
            <>
              共 <span className="font-medium text-ink-soft">{total}</span> 个模板
            </>
          )}
        </p>
      </div>

      {builtins.length > 0 && (
        <section className="mb-8">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-ui font-medium text-ink-soft">
              内置模板
              <span className="ml-1.5 text-micro font-normal text-ink-faint">
                {builtins.length} 个 · 只读
              </span>
            </h2>
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink-soft hover:bg-surface-subtle">
          <Icon name="plus" className="h-3.5 w-3.5" />
          导入模板
          <input
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              e.target.value = ''
              if (!file) return
              void (async () => {
                try {
                  const text = await file.text()
                  const payload = JSON.parse(text) as TemplateExportPayload
                  await importAgentTemplate(payload)
                  toast.success('已导入为你的私有模板')
                  invalidate()
                } catch (err) {
                  setError(extractError(err))
                }
              })()
            }}
          />
        </label>
        <p className="text-micro text-ink-faint">
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
            <h2 className="text-ui font-medium text-ink-soft">
              我的模板
              <span className="ml-1.5 text-micro font-normal text-ink-faint">
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
                onInspect={() => setInspecting(t)}
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
            key={editing.id}
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
            // 错误内联进表单：Modal 经 portal 挂在 body，页面级横幅在
            // 遮罩下面，用户点了保存却看不到任何反馈
            error={error}
          />
        </Modal>
      )}

      {teamShared.length > 0 && (
        <section>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="text-ui font-medium text-ink-soft">
              团队共享
              <span className="ml-1.5 text-micro font-normal text-ink-faint">
                {teamShared.length} 个 · 可用不可改
              </span>
            </h2>
          </div>
          <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {teamShared.map((t) => (
              <TemplateCard
                key={t.id}
                template={t}
                onUse={() => setUsing(t)}
                onEdit={null}
                onDelete={null}
                onInspect={null}
              />
            ))}
          </ul>
        </section>
      )}

      {inspecting && (
        <TemplateInspector
          template={inspecting}
          onClose={() => setInspecting(null)}
          onChanged={invalidate}
        />
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
            key={using.id}
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
            error={error}
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
  onInspect,
}: {
  template: AgentTemplate
  onUse: () => void
  onEdit: (() => void) | null
  onDelete: (() => void) | null
  /** 023：历史 / 共享 / 导出（仅自己的模板） */
  onInspect?: (() => void) | null
}) {
  return (
    <Card padding="none" interactive className="group overflow-hidden">
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          {/* 与 Agent 卡同款 40px 渐变底板：模板是「Agent 的胚子」，
              视觉上用同一套身份语言 */}
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-sky-400 via-brand-500 to-brand-600 text-white shadow-card ring-1 ring-inset ring-white/25">
            <Icon name="layers" className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate text-body font-semibold tracking-tight text-ink">
                {template.name}
              </h3>
              {template.is_builtin && <Badge tone="slate">内置</Badge>}
              {!template.is_builtin && template.visibility === 'team' && (
                <Badge tone="sky">团队共享</Badge>
              )}
            </div>
            <p className="mt-1 line-clamp-2 text-ui leading-relaxed text-ink-muted">
              {template.description || '暂无描述'}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" variant="primary" leftIcon="plus" onClick={onUse}>
            用这个模板
          </Button>
          {onEdit && (
            <Button size="sm" variant="secondary" leftIcon="edit" onClick={onEdit}>
              编辑
            </Button>
          )}
          {onInspect && (
            <Button size="sm" variant="ghost" onClick={onInspect}>
              历史 / 共享
            </Button>
          )}
          {onDelete && (
            <Button size="sm" variant="ghost" tone="danger" leftIcon="trash" onClick={onDelete}>
              删除
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line-subtle bg-surface-muted/60 px-4 py-2.5">
        {/* 元信息胶囊化，与 Agent 列表同一套形状语言 */}
        <span className="inline-flex items-center gap-1.5 rounded-md bg-surface px-2 py-1 font-mono text-micro font-medium text-ink-soft ring-1 ring-inset ring-line">
          {template.model}
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-md bg-surface px-2 py-1 text-micro text-ink-muted ring-1 ring-inset ring-line">
          <Icon name="layers" className="h-3 w-3 text-ink-faint" />
          {template.tools.length > 0 ? `${template.tools.length} 个工具` : '无工具'}
        </span>
        <span className="inline-flex items-center gap-1.5 rounded-md bg-surface px-2 py-1 text-micro text-ink-muted ring-1 ring-inset ring-line">
          <Icon name="coins" className="h-3 w-3 text-ink-faint" />
          上限 ¥{(template.cost_limit_cents / 100).toFixed(2)}
        </span>
        <span className="ml-auto hidden text-micro text-ink-faint sm:block">
          温度 {template.temperature.toFixed(1)} · 最多 {template.max_steps} 步 · 超时{' '}
          {template.max_run_seconds}s
        </span>
      </div>
    </Card>
  )
}


/**
 * 模板的「历史 / 共享 / 导出」弹层（023）。
 *
 * 三件事放一起的理由：它们都是「这个模板作为资产怎么管理」的动作。
 * 版本历史单独开一页的话，用户要先选模板再进页面——多一步且没人会去。
 */
function TemplateInspector({
  template,
  onClose,
  onChanged,
}: {
  template: AgentTemplate
  onClose: () => void
  onChanged: () => void
}) {
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)
  const [visibility, setVisibility] = useState(template.visibility)

  const { data: versions = [], refetch } = useQuery({
    queryKey: ['template-versions', template.id],
    queryFn: () => fetchTemplateVersions(template.id),
  })

  const shareMut = useMutation({
    mutationFn: (next: 'private' | 'team') =>
      updateAgentTemplate(template.id, { visibility: next }),
    onSuccess: (updated) => {
      setVisibility(updated.visibility)
      toast.success(
        updated.visibility === 'team' ? '已共享给团队（成员可用、不可改）' : '已收回共享'
      )
      onChanged()
    },
    onError: (err) => setError(extractError(err)),
  })

  const restoreMut = useMutation({
    mutationFn: (version: number) => restoreTemplateVersion(template.id, version),
    onSuccess: () => {
      toast.success('已回滚到该版本（会保留一条新历史）')
      void refetch()
      onChanged()
    },
    onError: (err) => setError(extractError(err)),
  })

  async function download() {
    try {
      const payload = await exportAgentTemplate(template.id)
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${template.name}.agent-template.json`
      link.click()
      URL.revokeObjectURL(url)
      toast.success('已导出 JSON')
    } catch (err) {
      setError(extractError(err))
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`模板设置：${template.name}`}
      description="共享给人用；历史留给你改坏时回去。"
      size="xl"
    >
      <div className="space-y-4">
        {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

        <div className="rounded-lg border border-line-subtle p-3">
          <p className="mb-2 text-ui font-medium text-ink-soft">共享范围</p>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant={visibility === 'private' ? 'primary' : 'secondary'}
              loading={shareMut.isPending}
              onClick={() => shareMut.mutate('private')}
            >
              私有（只有我能用）
            </Button>
            <Button
              size="sm"
              variant={visibility === 'team' ? 'primary' : 'secondary'}
              loading={shareMut.isPending}
              onClick={() => shareMut.mutate('team')}
            >
              团队共享（成员可用不可改）
            </Button>
            <Button size="sm" variant="ghost" leftIcon="download" onClick={() => void download()}>
              导出 JSON
            </Button>
          </div>
        </div>

        <div>
          <p className="mb-2 text-ui font-medium text-ink-soft">
            历史版本
            <span className="ml-1.5 text-micro font-normal text-ink-faint">
              {versions.length} 个 · 保留最近 20 个
            </span>
          </p>
          {versions.length === 0 ? (
            <p className="text-xs text-ink-faint">
              还没有版本——每次编辑会在保存前记一份快照。
            </p>
          ) : (
            <ul className="space-y-1">
              {versions.map((v: TemplateVersion) => (
                <li
                  key={v.id}
                  className="flex items-center gap-2 rounded-lg border border-line px-2.5 py-1.5 text-ui"
                >
                  <span className="font-mono text-ink">v{v.version}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-faint">
                    {new Date(v.created_at).toLocaleString('zh-CN')}
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={restoreMut.isPending}
                    onClick={() => restoreMut.mutate(v.version)}
                  >
                    回滚到此版本
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Modal>
  )
}
