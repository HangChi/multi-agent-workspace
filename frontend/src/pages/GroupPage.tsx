/** 组管理页（T038）：列表/创建/成员管理/排序/路由策略。 */

import { useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
import { dictKeys } from '../api/queryKeys'
import {
  addGroupMember,
  createGroup,
  deleteGroup,
  fetchAgents,
  fetchGroups,
  Group,
  reorderGroupMembers,
  removeGroupMember,
  RoutingStrategy,
  updateGroup,
} from '../api/endpoints'
import { Badge } from '../components/ui/Badge'
import { Button, IconButton } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { useConfirm } from '../components/ui/useConfirm'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Input, Select } from '../components/ui/Field'
import { Icon } from '../components/ui/Icons'
import type { IconName } from '../components/ui/Icons'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { Pagination } from '../components/ui/Pagination'
import { OptionCards } from '../components/ui/Segmented'
import { CardSkeleton } from '../components/ui/Skeleton'
import { useToast } from '../components/ui/useToast'
import { cn } from '../components/ui/cn'

/**
 * 成员编排预览：把「策略 + 顺序」画成一行可扫读的流程图。
 *
 * 组页最容易说不清的一件事是「这几个 Agent 到底怎么配合」——
 * 文字描述（见 STRATEGY_META.desc）是解释，这行图是结论。
 * 流水线画成箭头、广播画成分叉、路由器画成分派，一眼就能对上是哪种。
 */
function MemberFlow({
  strategy,
  members,
}: {
  strategy: RoutingStrategy
  members: { agent_id: string; name: string }[]
}) {
  if (members.length === 0) {
    return <p className="text-micro text-ink-faint">还没有成员 —— 展开「管理成员」加入 Agent。</p>
  }

  return (
    <ol className="flex flex-wrap items-center gap-1.5">
      {members.map((m, i) => (
        <li key={m.agent_id} className="flex items-center gap-1.5">
          {i > 0 && (
            <Icon
              name={strategy === 'pipeline' ? 'arrow-right' : 'plus'}
              className={cn(
                'h-3 w-3 shrink-0',
                strategy === 'pipeline' ? 'text-brand-400' : 'text-ink-faint'
              )}
            />
          )}
          <span className="inline-flex h-6 max-w-[10rem] items-center gap-1.5 rounded-md border border-line bg-surface px-1.5 text-micro font-medium text-ink-soft shadow-card">
            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-gradient-to-br from-sky-500 to-brand-600 text-2xs font-semibold text-white">
              {m.name.slice(0, 1)}
            </span>
            <span className="truncate">{m.name}</span>
          </span>
        </li>
      ))}
      {strategy === 'router' && (
        <>
          <Icon name="chevron-right" className="h-3 w-3 shrink-0 text-ink-faint" />
          <span className="inline-flex h-6 items-center rounded-md border border-dashed border-line-strong px-1.5 text-micro text-ink-muted">
            按内容选一个
          </span>
        </>
      )}
      {strategy === 'fan_out' && (
        <>
          <Icon name="chevron-right" className="h-3 w-3 shrink-0 text-ink-faint" />
          <span className="inline-flex h-6 items-center rounded-md border border-dashed border-line-strong px-1.5 text-micro text-ink-muted">
            汇总输出
          </span>
        </>
      )}
    </ol>
  )
}

const STRATEGY_META: Record<
  RoutingStrategy,
  { label: string; desc: string; short: string; icon: IconName }
> = {
  direct: {
    label: '直接路由',
    short: '直接',
    desc: '请求直接交给该 Agent，不做额外分派',
    icon: 'target',
  },
  fan_out: {
    label: '广播',
    short: '广播',
    desc: '同时发给所有成员，汇总各自的输出',
    icon: 'users',
  },
  pipeline: {
    label: '流水线',
    short: '流水线',
    desc: '按成员顺序串联执行，前序输出作为后序输入',
    icon: 'arrow-right',
  },
  router: {
    label: '路由器',
    short: '路由器',
    desc: '由模型阅读需求后挑选最合适的一个成员',
    icon: 'sparkles',
  },
}

const STRATEGY_OPTIONS = (Object.keys(STRATEGY_META) as RoutingStrategy[]).map((value) => ({
  value,
  label: STRATEGY_META[value].label,
  description: STRATEGY_META[value].desc,
  icon: STRATEGY_META[value].icon,
}))

interface GroupPayload {
  name: string
  description: string | null
  routing_strategy: RoutingStrategy
}

/** 每页组数。组卡片高度大（展开后有成员列表），比 Agent 页少放几个。 */
const PAGE_SIZE = 8

export function GroupPage() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Group | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  /** 组名筛选（后端 /groups 无 q 参数——只过滤当前页，文案说清范围） */
  const [q, setQ] = useState('')

  const { data: groups, isLoading } = useQuery({
    queryKey: ['groups', 'list', page],
    queryFn: () => fetchGroups({ page, page_size: PAGE_SIZE }),
    // 翻页时先留住上一页，避免列表闪成骨架屏（P7）
    placeholderData: keepPreviousData,
  })

  // ⚠️ all: true —— 这是「加入组」的成员候选列表，必须拿全部活跃 Agent。
  // 分页截断会让第 N 页之后的 Agent 无法被加入任何组，用户完全找不到原因。
  const { data: agents } = useQuery({
    queryKey: dictKeys.activeAgents,
    queryFn: () => fetchAgents({ status: 'active', all: true }),
  })

  const activeAgents = agents?.items ?? []
  const groupsTotal = groups?.total ?? 0

  /** 本页筛选（后端 /groups 无 q 参数；范围写进 placeholder，不误导） */
  const pageKeyword = q.trim().toLowerCase()
  const shownGroups = (groups?.items ?? []).filter(
    (g) =>
      !pageKeyword ||
      g.name.toLowerCase().includes(pageKeyword) ||
      (g.description ?? '').toLowerCase().includes(pageKeyword)
  )

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['groups'] })
  }

  const createMut = useMutation({
    mutationFn: createGroup,
    onSuccess: () => {
      invalidate()
      setShowForm(false)
      toast.success('组已创建')
    },
    onError: (err) => setError(extractError(err)),
  })

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: GroupPayload }) =>
      updateGroup(id, payload),
    onSuccess: () => {
      invalidate()
      setEditing(null)
      toast.success('组信息已更新')
    },
    onError: (err) => setError(extractError(err)),
  })

  const deleteMut = useMutation({
    mutationFn: deleteGroup,
    onSuccess: () => {
      invalidate()
      setExpanded(null)
      toast.success('组已删除')
    },
    onError: (err) => setError(extractError(err)),
  })

  const addMemberMut = useMutation({
    // 后端要求 position 唯一且自由；写死 999 会让第二个成员必然 409。
    // 追加语义由调用方按当前最大 position + 1 计算。
    mutationFn: ({
      groupId,
      agentId,
      position,
    }: {
      groupId: string
      agentId: string
      position: number
    }) => addGroupMember(groupId, { agent_id: agentId, position }),
    onSuccess: () => {
      invalidate()
      toast.success('成员已加入')
    },
    onError: (err) => setError(extractError(err)),
  })

  const removeMemberMut = useMutation({
    mutationFn: ({ groupId, agentId }: { groupId: string; agentId: string }) =>
      removeGroupMember(groupId, agentId),
    onSuccess: invalidate,
    onError: (err) => setError(extractError(err)),
  })

  const reorderMut = useMutation({
    mutationFn: ({
      groupId,
      positions,
    }: {
      groupId: string
      positions: { agent_id: string; position: number }[]
    }) => reorderGroupMembers(groupId, positions),
    onSuccess: invalidate,
    onError: (err) => setError(extractError(err)),
  })

  const moveMember = (group: Group, agentId: string, dir: -1 | 1) => {
    const sorted = [...group.members].sort((a, b) => a.position - b.position)
    const idx = sorted.findIndex((m) => m.agent_id === agentId)
    const target = idx + dir
    if (idx < 0 || target < 0 || target >= sorted.length) return
    const [item] = sorted.splice(idx, 1)
    sorted.splice(target, 0, item)
    const positions = sorted.map((m, i) => ({ agent_id: m.agent_id, position: i }))
    reorderMut.mutate({ groupId: group.id, positions })
  }

  /**
   * 表单开关互斥：新建与编辑是同一个 GroupEditor 组件，两个 state 同时
   * 为真时会渲染出两个叠在一起的表单（字段还会互相串）。
   */
  const openCreate = () => {
    setEditing(null)
    setShowForm(true)
    setError(null)
  }
  const openEdit = (group: Group) => {
    setShowForm(false)
    setEditing(group)
    setError(null)
  }

  const onSubmitCreate = (values: GroupPayload) => createMut.mutate(values)

  const onDelete = async (group: Group) => {
    const ok = await confirm({
      title: `删除组「${group.name}」？`,
      description: '仅删除组本身，组内的 Agent 不会被删除，历史运行记录保留。',
      confirmText: '删除',
      tone: 'danger',
    })
    if (ok) deleteMut.mutate(group.id)
  }

  const onRemoveMember = async (group: Group, agentId: string, name: string) => {
    const ok = await confirm({
      title: `从组中移除「${name}」？`,
      description: '移除后该成员不再参与此组的编排，Agent 本身不受影响。',
      confirmText: '移除',
      tone: 'warning',
    })
    if (ok) removeMemberMut.mutate({ groupId: group.id, agentId })
  }

  return (
    <Page>
      <PageHeader
        eyebrow="工作台"
        title="Agent 组管理"
        description="把多个 Agent 编成一组，按路由策略协调执行。"
        icon={<Icon name="users" />}
        actions={
          <div className="flex items-center gap-2">
            {showForm && (
              <Button variant="secondary" leftIcon="chevron-up" onClick={() => setShowForm(false)}>
                收起
              </Button>
            )}
            <Button variant="primary" leftIcon="plus" onClick={openCreate}>
              新建组
            </Button>
          </div>
        }
      />

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {showForm && (
        <GroupEditor
          key="new"
          onSubmit={onSubmitCreate}
          onCancel={() => setShowForm(false)}
          submitting={createMut.isPending}
        />
      )}

      {editing && (
        <GroupEditor
          // key 按编辑对象走：连点两个「编辑」不会把 A 的字段留给 B
          // （组件不卸载、useState 初始化器不重跑——静默数据覆盖）
          key={editing.id}
          group={editing}
          onSubmit={(payload) => updateMut.mutate({ id: editing.id, payload })}
          onCancel={() => setEditing(null)}
          submitting={updateMut.isPending}
        />
      )}

      {!isLoading && groupsTotal > 0 && (
        <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-micro text-ink-faint">
            共 <span className="font-medium text-ink-soft">{groupsTotal}</span> 个组 · 每张卡片上
            <span className="text-ink-soft">成员流程</span>按当前策略从左到右执行
          </p>
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="筛选本页组…"
            leftIcon="search"
            controlSize="sm"
            aria-label="筛选本页组"
            wrapperClassName="w-full sm:w-56"
            rightSlot={
              q ? (
                <IconButton
                  icon="x"
                  label="清除筛选"
                  size="xs"
                  onClick={() => setQ('')}
                  className="mr-0.5"
                />
              ) : undefined
            }
          />
        </div>
      )}

      {isLoading ? (
        <div className="space-y-3">
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : groupsTotal === 0 ? (
        <EmptyState
          icon="users"
          title="还没有 Agent 组"
          description="组适合多 Agent 协作：广播让多人各做一版、流水线让上游产出喂给下游、路由器按内容挑人。"
          action={
            <Button variant="primary" leftIcon="plus" onClick={() => setShowForm(true)}>
              新建第一个组
            </Button>
          }
        />
      ) : (
        <div className="space-y-3">
          {shownGroups.map((group) => {
            const isExpanded = expanded === group.id
            const sortedMembers = [...group.members].sort((a, b) => a.position - b.position)
            const availableAgents = activeAgents.filter(
              (a) => !group.members.some((m) => m.agent_id === a.id)
            )
            const meta = STRATEGY_META[group.routing_strategy]

            return (
              <Card key={group.id} padding="none" className="overflow-hidden">
                <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="flex min-w-0 items-start gap-3">
                    {/* 与 Agent 卡同款 40px 渐变底板：两个列表页并排看时
                       「头像」是同一套视觉语言 */}
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-sky-400 via-brand-500 to-brand-600 text-white shadow-card ring-1 ring-inset ring-white/25">
                      <Icon name="users" className="h-5 w-5" />
                    </span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-body font-semibold tracking-tight text-ink">
                          {group.name}
                        </h3>
                        <Badge tone="sky" dot>
                          {meta.label}
                        </Badge>
                        <Badge tone="slate">{group.members.length} 个成员</Badge>
                      </div>
                      <p
                        className={cn(
                          'mt-1 line-clamp-2 text-ui leading-relaxed',
                          group.description ? 'text-ink-muted' : 'text-ink-faint'
                        )}
                      >
                        {group.description || '暂无描述'}
                      </p>

                      {/* 编排顺序：折叠态也能看懂这个组怎么跑 */}
                      <div className="mt-2.5 overflow-hidden rounded-lg border border-line/70 bg-surface-muted/60 px-2 py-1.5">
                        <MemberFlow strategy={group.routing_strategy} members={sortedMembers} />
                      </div>
                    </div>
                  </div>

                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      variant={isExpanded ? 'outline' : 'secondary'}
                      leftIcon="users"
                      onClick={() => setExpanded(isExpanded ? null : group.id)}
                    >
                      {isExpanded ? '收起' : '管理成员'}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      leftIcon="edit"
                      onClick={() => openEdit(group)}
                    >
                      编辑
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      tone="danger"
                      leftIcon="trash"
                      onClick={() => void onDelete(group)}
                    >
                      删除
                    </Button>
                  </div>
                </div>

                {isExpanded && (
                  <div className="border-t border-line-subtle bg-surface-muted/60 p-4 animate-fade-in">
                    <div className="mb-3 flex items-start gap-2.5 rounded-lg border border-sky-200 bg-sky-50/60 px-3 py-2.5 dark:border-sky-500/25 dark:bg-sky-500/10">
                      <Icon name={meta.icon} className="mt-0.5 h-4 w-4 shrink-0 text-sky-500" />
                      <div className="min-w-0">
                        <p className="text-ui font-medium text-sky-900 dark:text-sky-200">
                          {meta.label}
                          <span className="ml-1.5 font-normal text-sky-600/80 dark:text-sky-300/80">
                            {group.members.length} 个成员
                          </span>
                        </p>
                        <p className="mt-0.5 text-xs leading-relaxed text-sky-700/80 dark:text-sky-300/80">
                          {meta.desc}
                        </p>
                      </div>
                      <Button
                        size="xs"
                        variant="ghost"
                        className="ml-auto shrink-0 text-sky-700 hover:bg-sky-100"
                        onClick={() => openEdit(group)}
                      >
                        修改策略
                      </Button>
                    </div>

                    {sortedMembers.length === 0 ? (
                      <div className="mb-3 rounded-lg border border-dashed border-line-strong bg-surface/60 py-4 text-center text-xs text-ink-faint">
                        暂无成员，从下方选择 Agent 加入
                      </div>
                    ) : (
                      <div className="mb-3 space-y-1.5">
                        {sortedMembers.map((m, i) => (
                          <div
                            key={m.agent_id}
                            className="flex items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-1.5 transition-colors hover:border-line-strong"
                          >
                            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-surface-subtle text-micro font-medium tabular-nums text-ink-muted">
                              {i + 1}
                            </span>
                            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-sky-500 to-brand-600 text-micro font-semibold text-white">
                              {m.name.slice(0, 1)}
                            </span>
                            <span className="min-w-0 flex-1 truncate text-ui font-medium text-ink-soft">
                              {m.name}
                            </span>
                            <div className="flex shrink-0 items-center">
                              <IconButton
                                icon="arrow-up"
                                label={`上移 ${m.name}`}
                                size="xs"
                                disabled={i === 0}
                                onClick={() => moveMember(group, m.agent_id, -1)}
                              />
                              <IconButton
                                icon="arrow-down"
                                label={`下移 ${m.name}`}
                                size="xs"
                                disabled={i === sortedMembers.length - 1}
                                onClick={() => moveMember(group, m.agent_id, 1)}
                              />
                              <IconButton
                                icon="x"
                                label={`移除 ${m.name}`}
                                size="xs"
                                variant="danger"
                                onClick={() => void onRemoveMember(group, m.agent_id, m.name)}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                      <Select
                        value=""
                        onChange={(e) => {
                          const agentId = e.target.value
                          if (agentId) {
                            const nextPosition =
                              group.members.reduce((max, m) => Math.max(max, m.position), -1) + 1
                            addMemberMut.mutate({
                              groupId: group.id,
                              agentId,
                              position: nextPosition,
                            })
                          }
                        }}
                        controlSize="sm"
                        placeholder={
                          availableAgents.length === 0
                            ? '没有可加入的活跃 Agent'
                            : '选择 Agent 加入此组…'
                        }
                        disabled={availableAgents.length === 0 || addMemberMut.isPending}
                        aria-label="添加成员"
                        wrapperClassName="flex-1"
                      >
                        {availableAgents.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name}（{a.model}）
                          </option>
                        ))}
                      </Select>
                      <p className="shrink-0 text-micro text-ink-faint">顺序 = 流水线执行顺序</p>
                    </div>
                  </div>
                )}
              </Card>
            )
          })}
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={groupsTotal}
            // 翻页后收起展开态：上一页的 expanded id 在新页面没有意义
            onChange={(p) => {
              setExpanded(null)
              setPage(p)
            }}
            loading={isLoading}
          />
          {shownGroups.length === 0 && pageKeyword && (
            <p className="py-6 text-center text-ui text-ink-faint">
              本页没有匹配「{q.trim()}」的组——筛选只覆盖当前页，换个关键词或清空筛选试试。
            </p>
          )}
        </div>
      )}
    </Page>
  )
}

/** 新建/编辑组表单（两者字段完全一致，合并为一个组件）。 */
function GroupEditor({
  group,
  onSubmit,
  onCancel,
  submitting,
}: {
  group?: Group
  onSubmit: (payload: GroupPayload) => void
  onCancel: () => void
  submitting?: boolean
}) {
  const [name, setName] = useState(group?.name ?? '')
  const [description, setDescription] = useState(group?.description ?? '')
  const [strategy, setStrategy] = useState<RoutingStrategy>(group?.routing_strategy ?? 'direct')
  const [touched, setTouched] = useState(false)

  const nameError = touched && !name.trim() ? '请填写组名称' : null

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        setTouched(true)
        if (!name.trim()) return
        onSubmit({
          name: name.trim(),
          description: description.trim() ? description.trim() : null,
          routing_strategy: strategy,
        })
      }}
      className="rounded-xl border border-line/90 bg-surface shadow-card animate-fade-in-up"
      noValidate
    >
      <div className="flex items-center gap-2.5 border-b border-line-subtle px-5 py-3.5">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-sky-50 text-sky-600 ring-1 ring-inset ring-sky-100">
          <Icon name={group ? 'edit' : 'plus'} className="h-4 w-4" />
        </span>
        <h2 className="text-sm font-semibold text-ink">
          {group ? `编辑组「${group.name}」` : '新建 Agent 组'}
        </h2>
      </div>

      <div className="space-y-4 p-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Input
            label="组名称"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => setTouched(true)}
            error={nameError}
            placeholder="例如：客户支持组"
            maxLength={60}
          />
          <Input
            label="描述"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="这个组负责什么？"
            maxLength={200}
          />
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-ui font-medium text-ink-soft">路由策略</span>
            <span className="text-xs text-ink-faint">决定组成员如何协作</span>
          </div>
          <OptionCards
            options={STRATEGY_OPTIONS}
            value={strategy}
            onChange={setStrategy}
            columns={4}
            name="路由策略"
          />
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 rounded-b-xl border-t border-line-subtle bg-surface-muted/70 px-5 py-3">
        <Button type="button" variant="secondary" onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" loading={submitting} leftIcon="check">
          {submitting ? '提交中…' : group ? '保存修改' : '创建组'}
        </Button>
      </div>
    </form>
  )
}
