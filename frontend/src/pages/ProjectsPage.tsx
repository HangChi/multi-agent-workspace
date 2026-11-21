/** 项目管理页（008 T019）：列表 + 新建 + 行内改名 + 归档切换 + 删除。
 *
 * 权限两层（同 SchedulesPage 的纪律）：
 * - **角色层**：viewer 对 /projects 是 403 → 直接渲染 403 态不发请求；
 * - **归属层**：项目是单人归属（owner_id），他人项目在接口层就 404，
 *   本页只可能看到自己的，所以没有 SchedulesPage 那种逐条 canWrite。
 *
 * 删除文案必须说清「需求会保留、归属 cleared」——用户看到「删除」第一反应
 * 是「里面的需求是不是也没了」，不说清就会有人不敢用这个功能
 * （后端语义见 research R-02：SET NULL，与 004 删调度规则同款）。
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
import { createProject, deleteProject, fetchProjects, updateProject } from '../api/endpoints'
import type { Project } from '../api/endpoints'
import { MemberSection } from '../components/project/MemberSection'
import { ProjectTransferDialog } from '../components/project/ProjectTransferDialog'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Icon } from '../components/ui/Icons'
import { Input } from '../components/ui/Field'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { Pagination } from '../components/ui/Pagination'
import { CardSkeleton } from '../components/ui/Skeleton'
import { useConfirm } from '../components/ui/useConfirm'
import { useToast } from '../components/ui/useToast'
import { useAuth } from '../hooks/useAuth'
import { hasFinePointer } from '../utils/platform'

export function ProjectsPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const { user } = useAuth()
  const isViewer = user?.role === 'viewer'

  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [error, setError] = useState<string | null>(null)
  // 行内改名的项目 id 与草稿
  const [renamingId, setRenamingId] = useState<string | null>(null)
  // 分页：项目会随团队使用不断累积，管理页不该一次全渲染
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 20
  const [renameDraft, setRenameDraft] = useState('')
  // 017：待移交的项目（null = 弹层关闭）
  const [transferTarget, setTransferTarget] = useState<Project | null>(null)

  const { data, isLoading } = useQuery({
    // 010：默认 relevant（我创建的 + 共享给我的）——页面用「我的/共享的」
    // 徽章区分归属（s12 的 C2 现场：只查 mine 时成员看不到共享项目）
    queryKey: ['projects', 'list', 'relevant', page],
    queryFn: () => fetchProjects({ scope: 'relevant', page, page_size: PAGE_SIZE }),
    enabled: !isViewer,
  })
  const projects = data?.items ?? []
  const totalProjects = data?.total ?? 0

  // 删掉当页最后一条时页码会落到空页：退一页，而不是让用户看着空白列表
  useEffect(() => {
    if (!isLoading && page > 1 && projects.length === 0) setPage((p) => p - 1)
  }, [isLoading, page, projects.length])

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['projects'] })
    void qc.invalidateQueries({ queryKey: ['requests'] })
  }

  const createMut = useMutation({
    mutationFn: () =>
      createProject({
        name: newName.trim(),
        description: newDescription.trim() || null,
      }),
    onSuccess: () => {
      invalidate()
      setShowCreate(false)
      setNewName('')
      setNewDescription('')
      toast.success('项目已创建')
    },
    onError: (err) => setError(extractError(err)),
  })

  const renameMut = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => updateProject(id, { name }),
    onSuccess: () => {
      invalidate()
      setRenamingId(null)
      toast.success('已改名（关联需求的项目名同步更新）')
    },
    onError: (err) => setError(extractError(err)),
  })

  const statusMut = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'active' | 'archived' }) =>
      updateProject(id, { status }),
    onSuccess: (_data, vars) => {
      invalidate()
      toast.success(vars.status === 'archived' ? '已归档' : '已恢复')
    },
    onError: (err) => setError(extractError(err)),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteProject(id),
    onSuccess: () => {
      invalidate()
      toast.success('项目已删除，其下需求保留且归属已清空')
    },
    onError: (err) => setError(extractError(err)),
  })

  const onDelete = async (project: Project) => {
    const ok = await confirm({
      title: `删除项目「${project.name}」？`,
      description:
        project.request_count > 0
          ? `该项目下 ${project.request_count} 条需求会保留，但项目归属会被清空（变成「未归属」）。Run、经手记录与通知都不受影响。此操作不可撤销。`
          : '该项目下还没有需求。此操作不可撤销。',
      confirmText: '删除',
      tone: 'danger',
    })
    if (ok) deleteMut.mutate(project.id)
  }

  if (isViewer) {
    return (
      <Page>
        <PageHeader
          eyebrow="工作台"
          title="项目管理"
          description="项目是需求的业务容器：建项目、给需求归类、按项目筛选看板。"
          icon={<Icon name="layers" />}
        />
        <EmptyState
          icon="lock"
          title="无权访问"
          description="项目管理的读写都需要 member 或 admin 角色。你可以在看板上查看分派给你的需求。"
        />
      </Page>
    )
  }

  return (
    <Page>
      <PageHeader
        eyebrow="工作台"
        title="项目管理"
        description="项目是需求的业务容器：建项目、给需求归类、按项目筛选看板。"
        icon={<Icon name="layers" />}
        actions={
          <Button
            variant={showCreate ? 'secondary' : 'primary'}
            leftIcon={showCreate ? 'chevron-up' : 'plus'}
            onClick={() => {
              setShowCreate((v) => !v)
              setError(null)
            }}
          >
            {showCreate ? '收起表单' : '新建项目'}
          </Button>
        }
      />

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {showCreate && (
        <form
          className="space-y-3 rounded-xl border border-line bg-surface p-4 shadow-card"
          onSubmit={(e) => {
            e.preventDefault()
            if (newName.trim()) createMut.mutate()
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setShowCreate(false)
          }}
        >
          <Input
            label="项目名称"
            required
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="例如：官网改版"
            maxLength={100}
            labelExtra={<span className="tabular-nums text-ink-faint">{newName.length}/100</span>}
            autoFocus={hasFinePointer()}
          />
          <Input
            label="描述（可选）"
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
            placeholder="这个项目要达成什么"
            maxLength={2000}
            // 与名称一致的字数计数：此前只有名称有，两个字段标准不一
            labelExtra={
              <span className="tabular-nums text-ink-faint">{newDescription.length}/2000</span>
            }
          />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setShowCreate(false)}>
              取消
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={createMut.isPending}
              disabled={!newName.trim()}
            >
              创建项目
            </Button>
          </div>
        </form>
      )}

      {isLoading ? (
        <div className="space-y-2">
          <CardSkeleton lines={1} />
          <CardSkeleton lines={1} />
        </div>
      ) : projects.length === 0 ? (
        <EmptyState
          icon="layers"
          title="还没有项目"
          description="建一个项目（如「官网改版」），之后建需求时就可以归类，看板也能按项目筛选。"
          action={
            <Button variant="primary" leftIcon="plus" onClick={() => setShowCreate(true)}>
              新建项目
            </Button>
          }
        />
      ) : (
        <ul className="space-y-2">
          {projects.map((project) => (
            <li
              key={project.id}
              className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 shadow-card"
            >
              {renamingId === project.id ? (
                <form
                  className="flex flex-1 items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault()
                    if (renameDraft.trim()) {
                      renameMut.mutate({ id: project.id, name: renameDraft.trim() })
                    }
                  }}
                  // Esc 取消行内改名（与弹层契约一致）
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setRenamingId(null)
                  }}
                >
                  <Input
                    value={renameDraft}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    maxLength={100}
                    controlSize="sm"
                    wrapperClassName="flex-1"
                    aria-label="项目新名称"
                    autoFocus={hasFinePointer()}
                  />
                  <Button
                    type="submit"
                    variant="primary"
                    size="sm"
                    loading={renameMut.isPending}
                    disabled={!renameDraft.trim()}
                  >
                    保存
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => setRenamingId(null)}
                  >
                    取消
                  </Button>
                </form>
              ) : (
                <>
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-body font-semibold tracking-tight text-ink">
                        {project.name}
                      </span>
                      <Badge tone={project.status === 'active' ? 'emerald' : 'slate'} dot>
                        {project.status === 'active' ? '进行中' : '已归档'}
                      </Badge>
                      {/* 010：我的/共享的区分（owner_id 是唯一归属真相） */}
                      <Badge tone={project.owner_id === user?.id ? 'brand' : 'sky'}>
                        {project.owner_id === user?.id ? '我的' : '共享的'}
                      </Badge>
                    </p>
                    {project.description && (
                      <p className="mt-0.5 truncate text-ui text-ink-muted">
                        {project.description}
                      </p>
                    )}
                  </div>
                  <span className="shrink-0 text-ui tabular-nums text-ink-muted">
                    {project.request_count} 条需求 · {project.member_count} 人
                  </span>
                  {/* 操作组：左侧细分隔线与信息区隔开，三个 ghost 按钮
                      不再像散落在行尾的裸文字 */}
                  <div className="flex shrink-0 items-center gap-0.5 border-l border-line-subtle pl-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      leftIcon="edit"
                      onClick={() => {
                        setRenamingId(project.id)
                        setRenameDraft(project.name)
                      }}
                    >
                      改名
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      leftIcon={project.status === 'active' ? 'archive' : 'refresh'}
                      onClick={() =>
                        statusMut.mutate({
                          id: project.id,
                          status: project.status === 'active' ? 'archived' : 'active',
                        })
                      }
                    >
                      {project.status === 'active' ? '归档' : '恢复'}
                    </Button>
                    {/* 017：移交入口。owner 且至少还有另一个成员才给——
                        没有其他成员时移交必然 409，不给走不通的路 */}
                    {project.owner_id === user?.id && project.member_count > 1 && (
                      <Button
                        variant="ghost"
                        size="sm"
                        leftIcon="users"
                        onClick={() => setTransferTarget(project)}
                      >
                        移交
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      tone="danger"
                      leftIcon="trash"
                      onClick={() => void onDelete(project)}
                    >
                      删除
                    </Button>
                  </div>

                  {/* 010：成员区（owner 可管理，成员只读——后端同一谓词） */}
                  <div className="w-full border-t border-line-subtle pt-2">
                    <MemberSection projectId={project.id} ownerId={project.owner_id} />
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      {totalProjects > PAGE_SIZE && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={totalProjects}
          loading={isLoading}
          onChange={setPage}
        />
      )}

      {/* 017：移交弹层挂一次（不是每张卡片一个），project 由 state 决定 */}
      {transferTarget && (
        <ProjectTransferDialog
          project={transferTarget}
          open
          onClose={() => setTransferTarget(null)}
        />
      )}
    </Page>
  )
}
