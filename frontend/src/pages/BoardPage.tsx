/** 看板页（T047/T048 + T056-T058）：需求列表 + 创建 + 附件上传 + 拖拽分配。 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'

import { extractError } from '../api/client'
import {
  createRequest,
  fetchAgents,
  fetchGroups,
  fetchRequest,
  fetchProjects,
  fetchRequests,
  fetchUserOptions,
  openDownloadUrl,
  resolveAttachmentDownloadUrl,
  RoutingStrategy,
  WorkRequest,
} from '../api/endpoints'
import { Board } from '../components/board/Board'
import { AssignModal } from '../components/board/AssignModal'
import { DispatchPanel } from '../components/board/DispatchPanel'
import { DependencySection } from '../components/request/DependencySection'
import { RejectModal } from '../components/board/RejectModal'
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
import { useToast } from '../components/ui/useToast'
import { cn } from '../components/ui/cn'
import { useAcceptRequest, useAssignRequest, useRejectRequest } from '../hooks/useRequests'
import { useAuthStore } from '../stores/authStore'
import { useBoardStore } from '../stores/boardStore'

const PRIORITY_LABEL: Record<string, string> = {
  high: '高优先级',
  normal: '中优先级',
  low: '低优先级',
}

const STATUS_LABEL: Record<string, string> = {
  created: '待处理',
  pending: '待接收',
  accepted: '已接收',
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
  const currentUser = useAuthStore((s) => s.user)
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<WorkRequest | null>(null)

  /** 重拉单个需求的完整详情（经手链/dependencies 只有详情端有）。 */
  const refreshDetail = (id: string) => {
    void fetchRequest(id)
      .then(setDetail)
      .catch(() => undefined)
  }
  // 007：退回原因弹窗
  const [showReject, setShowReject] = useState(false)

  /**
   * 深链打开详情：`/?request=<id>`。
   *
   * 触发历史里的「查看需求」跳的就是这个地址。列表只取第一页，
   * 所以目标需求不一定在当前列表里——那种情况下用 id 单独取一次，
   * 否则链接点进来会静默什么都不发生（比报错更难排查）。
   */
  const [searchParams, setSearchParams] = useSearchParams()
  const deepLinkId = searchParams.get('request')
  // 008：项目筛选深链。uuid=具体项目，'none'=未归属，缺省=全部。
  // 与 request 深链共用一套 searchParams——刷新、分享、后退都保持。
  const projectParam = searchParams.get('project')
  const project = projectParam === 'none' || projectParam ? projectParam : null
  const closeDetail = () => {
    setDetail(null)
    if (deepLinkId) {
      const next = new URLSearchParams(searchParams)
      next.delete('request')
      setSearchParams(next, { replace: true })
    }
  }

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

  // 007：看板范围（relevant 默认 = 我创建的 + 分派给我的；mine = 仅我创建的）
  const [scope, setScope] = useState<'relevant' | 'mine'>('relevant')
  const {
    data,
    isLoading,
    isError: listError,
    refetch: refetchRequests,
  } = useQuery({
    queryKey: ['requests', scope, project],
    queryFn: () =>
      fetchRequests({
        scope,
        project_id: project && project !== 'none' ? project : undefined,
        project: project === 'none' ? 'none' : undefined,
      }),
  })

  // 007：员工拖放目标（第三类落点——转交处理责任）
  const { data: userOptions } = useQuery({
    queryKey: ['users', 'options'],
    queryFn: fetchUserOptions,
  })

  // 008：项目筛选下拉。scope=relevant——接收人的下拉必须含「派给他那件活
  // 所属的项目」（那可能是别人的项目，FR-010）。
  const { data: projectOptions } = useQuery({
    queryKey: ['projects', 'options', 'relevant'],
    queryFn: () => fetchProjects({ scope: 'relevant' }),
  })

  // 分配目标：活跃 Agent + 全部组。
  // ⚠️ 必须 all: true —— 这两个列表是**拖拽目标集合**，分页截断会让
  // 第 N 页之后的目标从右侧面板静默消失（用户以为 Agent 被删了）。
  const { data: agents } = useQuery({
    queryKey: ['agents', 'active', 'all'],
    queryFn: () => fetchAgents({ status: 'active', all: true }),
  })
  const { data: groups } = useQuery({
    queryKey: ['groups', 'all'],
    queryFn: () => fetchGroups({ all: true }),
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
      toast.success(
        pending?.target.kind === 'user' ? '已分派，对方会收到通知' : '已分配并进入执行队列'
      )
    }
  })

  const { accept, isPending: accepting } = useAcceptRequest((err) => {
    if (err) setError(err)
    else {
      setDetail(null)
      toast.success('已接收，现在可以把需求拖给 Agent 了')
    }
  })

  const { reject, isPending: rejecting } = useRejectRequest((err) => {
    if (err) setError(err)
    else {
      setDetail(null)
      setShowReject(false)
      toast.success('已退回，创建者会收到通知')
    }
  })

  const requests = data?.items ?? []
  const activeAgents = agents?.items ?? []
  const allGroups = groups?.items ?? []

  /**
   * 深链目标：先在当前列表里找，找不到再单独取一次。
   *
   * 必须能取到，否则「查看需求」链接点进来是一片安静的空看板——
   * 用户会以为需求被删了。两个来源都要兜住：列表里已有的直接命中，
   * 不在列表（看板只取第一页）的才发这个请求。只写后一半会漏掉前者，
   * 表现为「深链有时候管用有时候不管用」。
   */
  const inList = deepLinkId ? requests.find((r) => r.id === deepLinkId) : undefined
  const {
    data: deepLinkRequest,
    isError: deepLinkError,
  } = useQuery({
    queryKey: ['requests', 'detail', deepLinkId],
    queryFn: () => fetchRequest(deepLinkId!),
    enabled: !!deepLinkId && !inList,
    retry: false,
  })

  /**
   * 详情数据源优先级：detail（点卡片时单独拉取的完整详情，含经手链）>
   * 列表项 > 深链单取。
   *
   * 为什么要点卡片时多拉一次：经手链（dispatch_history）只有详情端点
   * 返回，列表为了 N+1 不返回——看板时间线要么完整要么不显示，
   * 「有时候有有时候没有」比没有更糟。列表刷新（invalidate）后
   * detail 仍保留，靠「操作成功后关弹层」避免陈旧数据。
   */
  const detailSource = detail ?? inList ?? deepLinkRequest
  const currentDetail = detailSource
    ? detail && detail.id === detailSource.id
      ? detail
      : (requests.find((r) => r.id === detailSource.id) ?? detailSource)
    : null

  const onProjectChange = (value: string | null) => {
    const next = new URLSearchParams(searchParams)
    if (value) {
      next.set('project', value)
    } else {
      next.delete('project')
    }
    setSearchParams(next, { replace: true })
  }

  const counts = {
    pending: requests.filter((r) => r.status === 'created').length,
    running: requests.filter((r) => r.status === 'running').length,
    done: requests.filter((r) => r.status === 'completed').length,
  }

  return (
    <Page>
      <PageHeader
        eyebrow="工作台"
        title="需求看板"
        description={
          scope === 'relevant'
            ? '我的需求与分派给我的需求同板显示；拖到员工卡是转交责任，拖到 Agent/组是启动执行。'
            : '仅显示我创建的需求。'
        }
        icon={<Icon name="grid" />}
        actions={
          <>
            {/* 007：范围切换（relevant 默认 / mine 兼容旧视图） */}
            <div
              role="group"
              aria-label="看板范围"
              className="hidden items-center gap-1 rounded-lg border border-line bg-surface p-0.5 shadow-card sm:flex"
            >
              {(
                [
                  ['relevant', '与我相关'],
                  ['mine', '我创建的'],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={scope === value}
                  onClick={() => setScope(value)}
                  className={cn(
                    'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40',
                    scope === value ? 'bg-brand-600 text-white' : 'text-ink-muted hover:text-ink'
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            {counts.running > 0 && (
              <span className="hidden items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink-muted shadow-card sm:flex">
                <span className="h-1.5 w-1.5 rounded-full bg-sky-500 animate-pulse-ring" />
                <span className="font-semibold tabular-nums text-ink">{counts.running}</span>
                个正在执行
              </span>
            )}
            <Button
              variant={showForm ? 'secondary' : 'primary'}
              leftIcon={showForm ? 'chevron-up' : 'plus'}
              onClick={() => {
                setShowForm((v) => !v)
                setError(null)
              }}
            >
              {showForm ? '收起表单' : '新建需求'}
            </Button>
          </>
        }
      />

      {error && !pending && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {/* 列表拉取失败 ≠ 空看板：失败时明确告知并可重试，
          否则每列「暂无需求」会把故障伪装成空数据 */}
      {listError && (
        <ErrorBanner
          error="需求列表加载失败，可能是网络或服务暂时不可用"
          retry={() => void refetchRequests()}
          className="mb-3"
        />
      )}

      {/* 深链目标既不在列表、单独也取不到：说明白，别让页面一片安静 */}
      {deepLinkError && (
        <ErrorBanner
          error={`未找到请求查看的需求（${deepLinkId}），它可能已被删除或你没有访问权限`}
          onDismiss={closeDetail}
          className="mb-3"
        />
      )}

      {showForm && (
        <RequestForm
          onSubmit={(values: RequestFormValues) =>
            createMut.mutate({
              title: values.title,
              description: values.description || null,
              expected_output: values.expected_output || null,
              priority: values.priority,
              project_id: values.project_id,
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
        users={userOptions?.items ?? []}
        projects={projectOptions?.items ?? []}
        project={project}
        onProjectChange={onProjectChange}
        isLoading={isLoading}
        onAssignStart={(r, t) => {
          // 新开一次分配先清掉上一次的失败错误：否则新弹窗一打开
          // 就挂着旧的错误文案，用户会误以为是当前这次失败
          setError(null)
          openAssign(r, t)
        }}
        onOpenDetail={(r) => {
          // 先用列表数据秒开弹层，再拉完整详情补经手链（失败则降级为列表数据）
          setDetail(r)
          void refreshDetail(r.id)
        }}
        emptyAction={
          <Button variant="primary" leftIcon="plus" onClick={() => setShowForm(true)}>
            新建第一张需求
          </Button>
        }
      />

      {/* 分配 / 分派确认弹窗 */}
      {pending && (
        <AssignModal
          // key 按「需求 + 目标」走：换目标重挂载，策略初值才不会沿用
          // 上一次的 state（组数据姗姗来迟时尤其明显）
          key={`${pending.request.id}:${pending.target.kind}:${pending.target.id}`}
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
          user={
            pending.target.kind === 'user'
              ? userOptions?.items.find((u) => u.id === pending.target.id)
              : undefined
          }
          submitting={assigning}
          error={error}
          onConfirm={({ strategy, note }: { strategy?: RoutingStrategy; note?: string }) => {
            setError(null)
            assign({
              requestId: pending.request.id,
              payload: {
                assignee_type: pending.target.kind,
                assignee_id: pending.target.id,
                strategy,
                note,
              },
              assigneeLabel:
                pending.target.kind === 'agent'
                  ? activeAgents.find((a) => a.id === pending.target.id)?.name
                  : pending.target.kind === 'group'
                    ? allGroups.find((g) => g.id === pending.target.id)?.name
                    : userOptions?.items.find((u) => u.id === pending.target.id)?.email,
            })
          }}
          onCancel={closeAssign}
        />
      )}

      {/* 需求详情 / 附件弹层 */}
      {currentDetail && (
        <Modal
          open
          onClose={closeDetail}
          size="lg"
          title={currentDetail.title}
          description={
            <span className="flex flex-wrap items-center gap-1.5 pt-1">
              <Badge tone={PRIORITY_TONE[currentDetail.priority] ?? 'slate'}>
                {PRIORITY_LABEL[currentDetail.priority] ?? currentDetail.priority}
              </Badge>
              <Badge tone="slate">
                {STATUS_LABEL[currentDetail.status] ?? currentDetail.status}
              </Badge>
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
            {/* 009：前置依赖管理（区块自带 blocked 提示；增删失败走错误横幅） */}
            <DependencySection
              requestId={currentDetail.id}
              blocked={currentDetail.blocked ?? false}
              dependencies={currentDetail.dependencies ?? []}
              onChanged={() => refreshDetail(currentDetail.id)}
            />

            {/* 007：分派信息 + 接收/退回 + 经手时间线（有分派语义的需求才显示） */}
            {(currentDetail.assignee_id || (currentDetail.dispatch_history ?? []).length > 0) && (
              <DispatchPanel
                request={currentDetail}
                currentUserId={currentUser?.id ?? ''}
                accepting={accepting}
                rejecting={rejecting}
                onAccept={() => accept(currentDetail.id)}
                onReject={() => setShowReject(true)}
              />
            )}

            {currentDetail.runs_summary && currentDetail.runs_summary.total > 0 && (
              <div>
                <p className="mb-1 text-ui font-medium text-ink-soft">运行结果</p>
                <p className="rounded-lg bg-surface-muted px-3 py-2.5 text-sm text-ink-soft">
                  {[
                    `${currentDetail.runs_summary.completed} 完成`,
                    currentDetail.runs_summary.failed > 0
                      ? `${currentDetail.runs_summary.failed} 失败`
                      : null,
                    currentDetail.runs_summary.cancelled > 0
                      ? `${currentDetail.runs_summary.cancelled} 取消`
                      : null,
                    currentDetail.runs_summary.active > 0
                      ? `${currentDetail.runs_summary.active} 进行中`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                  <span className="text-ink-faint">
                    {' '}
                    / 共 {currentDetail.runs_summary.total} 个
                  </span>
                </p>
              </div>
            )}

            {currentDetail.source_schedule_id && (
              <div>
                <p className="mb-1 text-ui font-medium text-ink-soft">来源</p>
                <p className="flex flex-wrap items-center gap-1.5 rounded-lg bg-surface-muted px-3 py-2.5 text-sm text-ink-soft">
                  <Icon name="clock" className="h-3.5 w-3.5 text-ink-faint" />
                  由定时规则
                  {currentDetail.source_schedule_name ? (
                    <span className="font-medium text-ink">
                      「{currentDetail.source_schedule_name}」
                    </span>
                  ) : null}
                  自动创建
                  <Link
                    to="/schedules"
                    className="ml-auto text-xs font-medium text-accent hover:underline"
                  >
                    查看规则
                  </Link>
                </p>
              </div>
            )}

            {currentDetail.description && (
              <div>
                <p className="mb-1 text-ui font-medium text-ink-soft">描述</p>
                <p className="whitespace-pre-wrap rounded-lg bg-surface-muted px-3 py-2.5 text-sm leading-relaxed text-ink-soft">
                  {currentDetail.description}
                </p>
              </div>
            )}

            {currentDetail.expected_output && (
              <div>
                <p className="mb-1 text-ui font-medium text-ink-soft">期望产出</p>
                <p className="rounded-lg border border-brand-100 bg-brand-50/60 px-3 py-2.5 text-sm leading-relaxed text-accent">
                  {currentDetail.expected_output}
                </p>
              </div>
            )}

            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-ui font-medium text-ink-soft">
                  附件
                  <span className="ml-1 text-xs font-normal text-ink-faint">
                    {currentDetail.attachments.length} 个
                  </span>
                </p>
                <span className="text-micro text-ink-faint">支持拖拽上传</span>
              </div>

              {currentDetail.attachments.length > 0 && (
                <ul className="mb-2 space-y-1.5">
                  {currentDetail.attachments.map((att) => (
                    <li
                      key={att.key}
                      className="group flex items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-2 transition-colors hover:border-line-strong"
                    >
                      <Icon name="file" className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
                      <span className="min-w-0 flex-1 truncate text-ui text-ink-soft">
                        {att.name}
                      </span>
                      <span className="shrink-0 text-micro tabular-nums text-ink-faint">
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
                onUploaded={() => {
                  toast.success('附件已上传')
                  // 弹层内容来自 state（detail），invalidate 不会更新它——
                  // 不重拉的话 toast 说成功、列表里却看不到新文件
                  refreshDetail(currentDetail.id)
                }}
              />
            </div>
          </div>
        </Modal>
      )}

      {/* 007：退回原因弹窗 */}
      {showReject && currentDetail && (
        <RejectModal
          request={currentDetail}
          submitting={rejecting}
          error={error}
          onConfirm={(reason) => reject({ requestId: currentDetail.id, reason })}
          onCancel={() => setShowReject(false)}
        />
      )}
    </Page>
  )
}
