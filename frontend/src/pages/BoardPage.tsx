/** 看板页（T047/T048 + T056-T058）：需求列表 + 创建 + 附件上传 + 拖拽分配。 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
import {
  createRequest,
  fetchAgents,
  fetchGroups,
  fetchRequests,
  RoutingStrategy,
  WorkRequest,
} from '../api/endpoints'
import { Board } from '../components/board/Board'
import { AssignModal } from '../components/board/AssignModal'
import { AttachmentUploader } from '../components/request/AttachmentUploader'
import { RequestForm, RequestFormValues } from '../components/request/RequestForm'
import { useAssignRequest } from '../hooks/useRequests'
import { useBoardStore } from '../stores/boardStore'

export function BoardPage() {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<WorkRequest | null>(null)

  const pending = useBoardStore((s) => s.pending)
  const openAssign = useBoardStore((s) => s.openAssign)
  const closeAssign = useBoardStore((s) => s.closeAssign)

  const { data, isLoading } = useQuery({
    queryKey: ['requests'],
    queryFn: () => fetchRequests(),
  })

  // 分配目标：活跃 Agent + 全部组
  const { data: agents } = useQuery({
    queryKey: ['agents', 'active'],
    queryFn: () => fetchAgents({ status: 'active' }),
  })
  const { data: groups } = useQuery({
    queryKey: ['groups'],
    queryFn: () => fetchGroups(),
  })

  const createMut = useMutation({
    mutationFn: createRequest,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['requests'] })
      setShowForm(false)
    },
    onError: (err) => setError(extractError(err)),
  })

  const { assign, isPending: assigning } = useAssignRequest((err) => {
    if (err) setError(err)
    else closeAssign()
  })

  const requests = data?.items ?? []
  const activeAgents = agents?.items ?? []
  const allGroups = groups?.items ?? []

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">需求看板</h1>
          <p className="mt-1 text-sm text-slate-500">
            创建需求卡片，拖拽到右侧 Agent/组即可分配执行
          </p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          {showForm ? '取消' : '+ 新建需求'}
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
        <RequestForm
          onSubmit={(values: RequestFormValues) =>
            createMut.mutate({
              title: values.title,
              description: values.description || null,
              expected_output: values.expected_output || null,
              priority: values.priority,
            })
          }
          onCancel={() => setShowForm(false)}
          submitting={createMut.isPending}
        />
      )}

      <Board
        requests={requests}
        agents={activeAgents}
        groups={allGroups}
        isLoading={isLoading}
        onAssignStart={openAssign}
      />

      {/* 分配确认弹窗 */}
      {pending && (
        <AssignModal
          request={pending.request}
          target={pending.target}
          agent={
            pending.target.kind === 'agent'
              ? activeAgents.find((a) => a.id === pending.target.id)
              : undefined
          }
          group={
            pending.target.kind === 'group'
              ? allGroups.find((g) => g.id === pending.target.id)
              : undefined
          }
          submitting={assigning}
          error={error}
          onConfirm={({ strategy }: { strategy: RoutingStrategy }) => {
            setError(null)
            assign({
              requestId: pending.request.id,
              payload: {
                assignee_type: pending.target.kind,
                assignee_id: pending.target.id,
                strategy,
              },
            })
          }}
          onCancel={closeAssign}
        />
      )}

      {/* 详情/附件弹层 */}
      {detail && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
          onClick={() => setDetail(null)}
        >
          <div
            className="max-h-[80vh] w-full max-w-lg overflow-y-auto rounded-xl bg-white p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-start justify-between">
              <h2 className="text-lg font-semibold text-slate-800">{detail.title}</h2>
              <button
                onClick={() => setDetail(null)}
                className="rounded-lg px-2 py-1 text-sm text-slate-400 hover:bg-slate-100"
              >
                ✕
              </button>
            </div>
            <div className="mb-3 flex gap-2 text-xs">
              <span className="rounded-full bg-blue-50 px-2 py-0.5 font-medium text-blue-700">
                优先级 {detail.priority}
              </span>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-600">
                {detail.status}
              </span>
            </div>
            {detail.description && (
              <p className="mb-3 text-sm text-slate-600">{detail.description}</p>
            )}
            {detail.expected_output && (
              <div className="mb-3 rounded-lg bg-slate-50 p-3">
                <p className="text-xs font-medium text-slate-500">期望产出</p>
                <p className="mt-1 text-sm text-slate-700">{detail.expected_output}</p>
              </div>
            )}

            <div className="mb-2">
              <p className="mb-2 text-sm font-medium text-slate-700">
                附件（{detail.attachments.length}）
              </p>
              <AttachmentUploader
                requestId={detail.id}
                onUploaded={() => setDetail(null)}
              />
              {detail.attachments.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {detail.attachments.map((att) => (
                    <li
                      key={att.key}
                      className="flex items-center justify-between rounded bg-slate-50 px-3 py-1.5 text-sm"
                    >
                      <span className="truncate text-slate-700">{att.name}</span>
                      <span className="ml-2 shrink-0 text-xs text-slate-400">
                        {(att.size / 1024).toFixed(1)} KB
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
