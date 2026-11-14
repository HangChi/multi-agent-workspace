/** Agent 管理页（T037）：列表/创建/编辑/归档/删除，TanStack Query 缓存。 */

import { useState } from 'react'
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

export function AgentPage() {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Agent | null>(null)
  const [error, setError] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: () => fetchAgents(),
  })

  const createMut = useMutation({
    mutationFn: createAgent,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['agents'] })
      setShowForm(false)
    },
    onError: (err) => setError(extractError(err)),
  })

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<AgentFormValues> }) =>
      updateAgent(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['agents'] })
      setEditing(null)
    },
    onError: (err) => setError(extractError(err)),
  })

  const archiveMut = useMutation({
    mutationFn: archiveAgent,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['agents'] }),
    onError: (err) => setError(extractError(err)),
  })

  const deleteMut = useMutation({
    mutationFn: deleteAgent,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['agents'] }),
    onError: (err) => setError(extractError(err)),
  })

  const onSubmit = (values: AgentFormValues) => {
    if (editing) {
      updateMut.mutate({ id: editing.id, payload: values })
    } else {
      createMut.mutate(values)
    }
  }

  const onArchive = (agent: Agent) => {
    if (window.confirm(`归档 Agent「${agent.name}」？`)) {
      archiveMut.mutate(agent.id)
    }
  }

  const onDelete = (agent: Agent) => {
    if (window.confirm(`删除 Agent「${agent.name}」？被组引用时将被拒绝。`)) {
      deleteMut.mutate(agent.id)
    }
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Agent 管理</h1>
          <p className="mt-1 text-sm text-slate-500">
            创建可执行的 Agent：提示词、模型、工具集、成本上限
          </p>
        </div>
        <button
          onClick={() => {
            setEditing(null)
            setShowForm((v) => !v)
          }}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          {showForm ? '取消' : '+ 新建 Agent'}
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-600">
          {error}
          <button
            onClick={() => setError(null)}
            className="ml-2 font-medium underline"
          >
            关闭
          </button>
        </div>
      )}

      {(showForm || editing) && (
        <div className="mb-6 rounded-xl border border-slate-200 bg-white p-6">
          <h2 className="mb-4 text-lg font-semibold text-slate-800">
            {editing ? `编辑 Agent「${editing.name}」` : '新建 Agent'}
          </h2>
          <AgentForm
            initial={editing ?? undefined}
            onSubmit={onSubmit}
            onCancel={() => {
              setShowForm(false)
              setEditing(null)
            }}
            submitting={createMut.isPending || updateMut.isPending}
          />
        </div>
      )}

      {isLoading ? (
        <div className="py-12 text-center text-slate-500">加载中...</div>
      ) : (
        <div className="space-y-3">
          {data?.items.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-300 bg-white py-12 text-center text-slate-500">
              还没有 Agent，点击右上角「新建 Agent」开始
            </div>
          ) : (
            data?.items.map((agent) => (
              <AgentListItem
                key={agent.id}
                agent={agent}
                onEdit={() => {
                  setEditing(agent)
                  setShowForm(true)
                }}
                onArchive={() => onArchive(agent)}
                onDelete={() => onDelete(agent)}
              />
            ))
          )}
        </div>
      )}
    </div>
  )
}
