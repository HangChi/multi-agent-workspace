/** 看板页（T047/T048 + T056-T058）：需求列表 + 创建 + 附件上传 + 拖拽分配。 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
import {
  createRequest,
  fetchAgents,
  fetchGroups,
  fetchRequests,
  openDownloadUrl,
  resolveAttachmentDownloadUrl,
  RoutingStrategy,
  WorkRequest,
} from '../api/endpoints'
import { Board } from '../components/board/Board'
import { AssignModal } from '../components/board/AssignModal'
import { AttachmentUploader } from '../components/request/AttachmentUploader'
import { RequestForm, RequestFormValues } from '../components/request/RequestForm'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { IconTile } from '../components/ui/Card'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Icon } from '../components/ui/Icons'
import { Modal } from '../components/ui/Modal'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { useToast } from '../components/ui/Toast'
import { useAssignRequest } from '../hooks/useRequests'
import { useBoardStore } from '../stores/boardStore'

const PRIORITY_LABEL: Record<string, string> = {
  high: '高优先级',
  normal: '中优先级',
  low: '低优先级',
}

const STATUS_LABEL: Record<string, string> = {
  created: '待分配',
  assigned: '已分配',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const PRIORITY_TONE = {
  high: 'rose',
  normal: 'brand',
  low: 'slate',
} as const

export function BoardPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<WorkRequest | null>(null)

  /** 附件下载：先带鉴权换预签名 URL，再跳转（直链会 401）。 */
  const downloadAttachment = async (requestId: string, key: string) => {
    try {
      const { url } = await resolveAttachmentDownloadUrl(requestId, key)
      openDownloadUrl(url)
    } catch (e) {
      toast.error('下载失败', extractError(e))
    }
  }

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
      setError(null)
      toast.success('需求已创建', '拖拽卡片到右侧 Agent 或组即可开始执行')
    },
    onError: (err) => setError(extractError(err)),
  })

  const { assign, isPending: assigning } = useAssignRequest((err) => {
    if (err) setError(err)
    else {
      closeAssign()
      toast.success('已分配并进入执行队列')
    }
  })

  const requests = data?.items ?? []
  const activeAgents = agents?.items ?? []
  const allGroups = groups?.items ?? []

  // 列表刷新后同步弹层里的数据（附件上传后会 invalidate）
  const currentDetail = detail ? (requests.find((r) => r.id === detail.id) ?? detail) : null

  const counts = {
    pending: requests.filter((r) => r.status === 'created').length,
    running: requests.filter((r) => r.status === 'running').length,
    done: requests.filter((r) => r.status === 'completed').length,
  }

  return (
    <Page>
      <PageHeader
        title="需求看板"
        description="创建需求卡片，拖拽到右侧 Agent 或组即可分配执行。"
        icon={
          <IconTile tone="brand" size="lg">
            <Icon name="grid" className="h-5 w-5" />
          </IconTile>
        }
        actions={
          <>
            <div className="hidden items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs shadow-card sm:flex">
              <span className="text-ink-muted">待分配</span>
              <span className="font-semibold tabular-nums text-ink">{counts.pending}</span>
              <span className="mx-1 h-3 w-px bg-line" />
              <span className="text-ink-muted">执行中</span>
              <span className="font-semibold tabular-nums text-sky-600">{counts.running}</span>
              <span className="mx-1 h-3 w-px bg-line" />
              <span className="text-ink-muted">已完成</span>
              <span className="font-semibold tabular-nums text-emerald-600">{counts.done}</span>
            </div>
            <Button
              variant={showForm ? 'secondary' : 'primary'}
              leftIcon={showForm ? 'chevron-up' : 'plus'}
              onClick={() => {
                setShowForm((v) => !v)
                setError(null)
              }}
            >
              {showForm ? '收起' : '新建需求'}
            </Button>
          </>
        }
      />

      {error && !pending && (
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
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
        onOpenDetail={setDetail}
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

      {/* 需求详情 / 附件弹层 */}
      {currentDetail && (
        <Modal
          open
          onClose={() => setDetail(null)}
          size="lg"
          title={currentDetail.title}
          description={
            <span className="flex flex-wrap items-center gap-1.5 pt-1">
              <Badge tone={PRIORITY_TONE[currentDetail.priority] ?? 'slate'}>
                {PRIORITY_LABEL[currentDetail.priority] ?? currentDetail.priority}
              </Badge>
              <Badge tone="slate">{STATUS_LABEL[currentDetail.status] ?? currentDetail.status}</Badge>
              <span className="text-xs text-ink-faint">
                创建于 {new Date(currentDetail.created_at).toLocaleString('zh-CN')}
              </span>
            </span>
          }
          icon={
            <IconTile tone="slate">
              <Icon name="file" className="h-[18px] w-[18px]" />
            </IconTile>
          }
          footer={
            <Button variant="secondary" onClick={() => setDetail(null)}>
              关闭
            </Button>
          }
        >
          <div className="space-y-4">
            {currentDetail.description && (
              <div>
                <p className="mb-1 text-[13px] font-medium text-ink-soft">描述</p>
                <p className="whitespace-pre-wrap rounded-lg bg-surface-muted px-3 py-2.5 text-sm leading-relaxed text-ink-soft">
                  {currentDetail.description}
                </p>
              </div>
            )}

            {currentDetail.expected_output && (
              <div>
                <p className="mb-1 text-[13px] font-medium text-ink-soft">期望产出</p>
                <p className="rounded-lg border border-brand-100 bg-brand-50/60 px-3 py-2.5 text-sm leading-relaxed text-accent">
                  {currentDetail.expected_output}
                </p>
              </div>
            )}

            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-[13px] font-medium text-ink-soft">
                  附件
                  <span className="ml-1 text-xs font-normal text-ink-faint">
                    {currentDetail.attachments.length} 个
                  </span>
                </p>
                <span className="text-2xs text-ink-faint">支持拖拽上传</span>
              </div>

              {currentDetail.attachments.length > 0 && (
                <ul className="mb-2 space-y-1.5">
                  {currentDetail.attachments.map((att) => (
                    <li
                      key={att.key}
                      className="group flex items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-2 transition-colors hover:border-line-strong"
                    >
                      <Icon name="file" className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
                      <span className="min-w-0 flex-1 truncate text-[13px] text-ink-soft">
                        {att.name}
                      </span>
                      <span className="shrink-0 text-2xs tabular-nums text-ink-faint">
                        {(att.size / 1024).toFixed(1)} KB
                      </span>
                      <button
                        type="button"
                        onClick={() => void downloadAttachment(currentDetail.id, att.key)}
                        aria-label={`下载 ${att.name}`}
                        className="shrink-0 rounded-md p-1 text-ink-faint transition-colors hover:bg-surface-subtle hover:text-accent"
                      >
                        <Icon name="download" className="h-3.5 w-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <AttachmentUploader
                requestId={currentDetail.id}
                onUploaded={() => toast.success('附件已上传')}
              />
            </div>
          </div>
        </Modal>
      )}
    </Page>
  )
}
