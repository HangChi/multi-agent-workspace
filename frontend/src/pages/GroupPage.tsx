/** 组管理页（T038）：列表/创建/成员管理/排序/路由策略。 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
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

const STRATEGY_LABELS: Record<RoutingStrategy, string> = {
  direct: '直接路由',
  fan_out: '广播',
  pipeline: '流水线',
  router: '路由器',
}

const STRATEGY_DESC: Record<RoutingStrategy, string> = {
  direct: '请求直接交给该 Agent',
  fan_out: '同时发给所有成员，汇总结果',
  pipeline: '按顺序串联执行，前序输出作为后序输入',
  router: '根据内容智能分发给最合适的成员',
}

export function GroupPage() {
  const qc = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Group | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const { data: groups, isLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => fetchGroups(),
  })

  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: () => fetchAgents({ status: 'active' }),
  })

  const activeAgents = agents?.items ?? []

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['groups'] })
  }

  const createMut = useMutation({
    mutationFn: createGroup,
    onSuccess: () => {
      invalidate()
      setShowForm(false)
    },
    onError: (err) => setError(extractError(err)),
  })

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: { name?: string; description?: string | null; routing_strategy?: RoutingStrategy } }) =>
      updateGroup(id, payload),
    onSuccess: () => {
      invalidate()
      setEditing(null)
    },
    onError: (err) => setError(extractError(err)),
  })

  const deleteMut = useMutation({
    mutationFn: deleteGroup,
    onSuccess: () => {
      invalidate()
      setExpanded(null)
    },
    onError: (err) => setError(extractError(err)),
  })

  const addMemberMut = useMutation({
    mutationFn: ({ groupId, agentId }: { groupId: string; agentId: string }) =>
      addGroupMember(groupId, {
        agent_id: agentId,
        position: 999, // 追加到末尾，后端会处理
      }),
    onSuccess: invalidate,
    onError: (err) => setError(extractError(err)),
  })

  const removeMemberMut = useMutation({
    mutationFn: ({ groupId, agentId }: { groupId: string; agentId: string }) =>
      removeGroupMember(groupId, agentId),
    onSuccess: invalidate,
    onError: (err) => setError(extractError(err)),
  })

  const reorderMut = useMutation({
    mutationFn: ({ groupId, positions }: { groupId: string; positions: { agent_id: string; position: number }[] }) =>
      reorderGroupMembers(groupId, positions),
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

  const onSubmitCreate = (values: { name: string; description: string; routing_strategy: RoutingStrategy }) => {
    createMut.mutate({
      name: values.name,
      description: values.description.trim() ? values.description.trim() : null,
      routing_strategy: values.routing_strategy,
    })
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Agent 组管理</h1>
          <p className="mt-1 text-sm text-slate-500">
            将多个 Agent 编组，按路由策略协调执行
          </p>
        </div>
        <button
          onClick={() => {
            setEditing(null)
            setShowForm((v) => !v)
          }}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          {showForm ? '取消' : '+ 新建组'}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
          <button onClick={() => setError(null)} className="ml-2 font-medium underline">
            关闭
          </button>
        </div>
      )}

      {showForm && (
        <GroupForm
          onSubmit={onSubmitCreate}
          onCancel={() => setShowForm(false)}
          submitting={createMut.isPending}
        />
      )}

      {editing && (
        <EditGroupForm
          group={editing}
          onSubmit={(payload) => updateMut.mutate({ id: editing.id, payload })}
          onCancel={() => setEditing(null)}
          submitting={updateMut.isPending}
        />
      )}

      {isLoading ? (
        <div className="py-12 text-center text-slate-500">加载中...</div>
      ) : groups?.items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white py-12 text-center text-slate-500">
          还没有 Agent 组，点击右上角「新建组」开始
        </div>
      ) : (
        <div className="space-y-3">
          {groups?.items.map((group) => {
            const isExpanded = expanded === group.id
            const sortedMembers = [...group.members].sort((a, b) => a.position - b.position)
            const availableAgents = activeAgents.filter(
              (a) => !group.members.some((m) => m.agent_id === a.id),
            )
            return (
              <div
                key={group.id}
                className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="truncate font-semibold text-slate-800">{group.name}</h3>
                      <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700">
                        {STRATEGY_LABELS[group.routing_strategy]}
                      </span>
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
                        {group.members.length} 个成员
                      </span>
                    </div>
                    {group.description && (
                      <p className="mt-1 line-clamp-2 text-sm text-slate-500">
                        {group.description}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <button
                      onClick={() => setExpanded(isExpanded ? null : group.id)}
                      className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                    >
                      {isExpanded ? '收起' : '管理成员'}
                    </button>
                    <button
                      onClick={() => setEditing(group)}
                      className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                    >
                      编辑
                    </button>
                    <button
                      onClick={() => {
                        if (window.confirm(`删除组「${group.name}」？成员不会被删除。`)) {
                          deleteMut.mutate(group.id)
                        }
                      }}
                      className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"
                    >
                      删除
                    </button>
                  </div>
                </div>

                {isExpanded && (
                  <div className="mt-4 border-t border-slate-100 pt-4">
                    <div className="mb-3">
                      <p className="text-sm font-medium text-slate-700">
                        路由策略：{STRATEGY_LABELS[group.routing_strategy]}
                      </p>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {STRATEGY_DESC[group.routing_strategy]}
                      </p>
                    </div>

                    {sortedMembers.length === 0 ? (
                      <p className="mb-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-400">
                        暂无成员，从下方添加
                      </p>
                    ) : (
                      <div className="mb-3 space-y-1.5">
                        {sortedMembers.map((m, i) => (
                          <div
                            key={m.agent_id}
                            className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
                          >
                            <div className="flex items-center gap-3">
                              <span className="w-5 text-center text-xs font-medium text-slate-400">
                                {i + 1}
                              </span>
                              <span className="text-sm font-medium text-slate-700">
                                {m.name}
                              </span>
                            </div>
                            <div className="flex items-center gap-1">
                              <button
                                onClick={() => moveMember(group, m.agent_id, -1)}
                                disabled={i === 0}
                                className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-500 hover:bg-white disabled:opacity-30"
                                title="上移"
                              >
                                ↑
                              </button>
                              <button
                                onClick={() => moveMember(group, m.agent_id, 1)}
                                disabled={i === sortedMembers.length - 1}
                                className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-500 hover:bg-white disabled:opacity-30"
                                title="下移"
                              >
                                ↓
                              </button>
                              <button
                                onClick={() => {
                                  if (window.confirm(`从组中移除「${m.name}」？`)) {
                                    removeMemberMut.mutate({
                                      groupId: group.id,
                                      agentId: m.agent_id,
                                    })
                                  }
                                }}
                                className="ml-1 rounded border border-red-200 px-2 py-0.5 text-xs text-red-500 hover:bg-red-50"
                              >
                                移除
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    <div className="flex items-center gap-2">
                      <select
                        className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        value=""
                        onChange={(e) => {
                          const agentId = e.target.value
                          if (agentId) {
                            addMemberMut.mutate({ groupId: group.id, agentId })
                          }
                        }}
                      >
                        <option value="">选择 Agent 加入此组...</option>
                        {availableAgents.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name}（{a.model}）
                          </option>
                        ))}
                        {availableAgents.length === 0 && (
                          <option value="" disabled>
                            没有可加入的活跃 Agent
                          </option>
                        )}
                      </select>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** 新建组表单。 */
function GroupForm({
  onSubmit,
  onCancel,
  submitting,
}: {
  onSubmit: (values: { name: string; description: string; routing_strategy: RoutingStrategy }) => void
  onCancel: () => void
  submitting?: boolean
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [strategy, setStrategy] = useState<RoutingStrategy>('direct')

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!name.trim()) return
        onSubmit({ name: name.trim(), description, routing_strategy: strategy })
      }}
      className="mb-6 rounded-xl border border-slate-200 bg-white p-6"
    >
      <h2 className="mb-4 text-lg font-semibold text-slate-800">新建 Agent 组</h2>
      <div className="space-y-4">
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">
            组名称 <span className="text-red-500">*</span>
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="例如：客户支持组"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">描述</label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="这个组负责什么？"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-2 block text-sm font-medium text-slate-700">路由策略</label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {(Object.keys(STRATEGY_LABELS) as RoutingStrategy[]).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setStrategy(s)}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  strategy === s
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-slate-200 hover:border-slate-300'
                }`}
              >
                <span className="block text-sm font-medium text-slate-800">
                  {STRATEGY_LABELS[s]}
                </span>
                <span className="mt-1 block text-xs text-slate-500">{STRATEGY_DESC[s]}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={submitting || !name.trim()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {submitting ? '提交中...' : '创建'}
          </button>
        </div>
      </div>
    </form>
  )
}

/** 编辑组表单（只改名称/描述/策略）。 */
function EditGroupForm({
  group,
  onSubmit,
  onCancel,
  submitting,
}: {
  group: Group
  onSubmit: (payload: {
    name?: string
    description?: string | null
    routing_strategy?: RoutingStrategy
  }) => void
  onCancel: () => void
  submitting?: boolean
}) {
  const [name, setName] = useState(group.name)
  const [description, setDescription] = useState(group.description ?? '')
  const [strategy, setStrategy] = useState<RoutingStrategy>(group.routing_strategy)

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!name.trim()) return
        onSubmit({
          name: name.trim(),
          description: description.trim() ? description.trim() : null,
          routing_strategy: strategy,
        })
      }}
      className="mb-6 rounded-xl border border-slate-200 bg-white p-6"
    >
      <h2 className="mb-4 text-lg font-semibold text-slate-800">
        编辑组「{group.name}」
      </h2>
      <div className="space-y-4">
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">
            组名称 <span className="text-red-500">*</span>
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">描述</label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-2 block text-sm font-medium text-slate-700">路由策略</label>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {(Object.keys(STRATEGY_LABELS) as RoutingStrategy[]).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setStrategy(s)}
                className={`rounded-lg border p-3 text-left transition-colors ${
                  strategy === s
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-slate-200 hover:border-slate-300'
                }`}
              >
                <span className="block text-sm font-medium text-slate-800">
                  {STRATEGY_LABELS[s]}
                </span>
                <span className="mt-1 block text-xs text-slate-500">{STRATEGY_DESC[s]}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={submitting || !name.trim()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {submitting ? '提交中...' : '保存'}
          </button>
        </div>
      </div>
    </form>
  )
}
