/** 项目成员区（010 T018）：项目管理页里的「谁在这个项目里」。
 *
 * 两级 UI（与后端三级谓词对应）：
 * - owner：成员列表 + 添加器（users/options 数据源）+ 移除按钮；
 * - 成员：只读列表（可见性是成员级，管理才是 owner 级）；
 * - 陌生人：根本看不到这个项目（后端 404，前端无入口）。
 *
 * 「我的/共享的」由父页面用 owner_id 判定，不在这里重复判断——
 * 一个项目只有一个归属真相。
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../../api/client'
import {
  addProjectMember,
  fetchProjectMembers,
  fetchUserOptions,
  removeProjectMember,
} from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button, IconButton } from '../ui/Button'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Icon } from '../ui/Icons'
import { Select } from '../ui/Field'
import { Skeleton } from '../ui/Skeleton'
import { useAuth } from '../../hooks/useAuth'
import { useToast } from '../ui/useToast'

interface Props {
  projectId: string
  /** 项目归属人（父页面已有，避免组件再查一次项目详情） */
  ownerId: string
}

export function MemberSection({ projectId, ownerId }: Props) {
  const qc = useQueryClient()
  const toast = useToast()
  const { user } = useAuth()
  const isOwner = user?.id === ownerId
  const [error, setError] = useState<string | null>(null)
  const [picked, setPicked] = useState('')

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['projectMembers', projectId],
    queryFn: () => fetchProjectMembers(projectId),
  })
  const members = data?.items ?? []

  // 添加器候选：在职用户（复用分派选人的同一数据源），排除已在项目里的
  const { data: users } = useQuery({
    queryKey: ['users', 'options'],
    queryFn: fetchUserOptions,
    enabled: isOwner,
  })
  const inProject = new Set(members.map((m) => m.user_id))
  const candidates = (users?.items ?? []).filter((u) => !inProject.has(u.id))

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['projectMembers', projectId] })
    void qc.invalidateQueries({ queryKey: ['projects'] })
  }

  const addMut = useMutation({
    mutationFn: (userId: string) => addProjectMember(projectId, userId),
    onSuccess: () => {
      invalidate()
      setPicked('')
      toast.success('已添加成员，对方会收到通知')
    },
    onError: (err) => setError(extractError(err)),
  })

  const removeMut = useMutation({
    mutationFn: (userId: string) => removeProjectMember(projectId, userId),
    onSuccess: () => {
      invalidate()
      toast.success('已移除成员')
    },
    onError: (err) => setError(extractError(err)),
  })

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-ui font-medium text-ink-soft">
          项目成员
          <span className="ml-1 text-xs font-normal text-ink-faint">{members.length} 人</span>
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-1.5" aria-hidden="true">
          <Skeleton className="h-9" />
          <Skeleton className="h-9" />
        </div>
      ) : isError ? (
        <ErrorBanner error="成员列表加载失败" retry={() => void refetch()} />
      ) : members.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line-strong/70 px-3 py-4 text-center text-xs text-ink-faint">
          还没有成员——项目目前只有创建者可见
        </p>
      ) : (
        <ul className="space-y-1.5">
          {members.map((m) => (
            <li
              key={m.user_id}
              className="flex items-center gap-2 rounded-lg border border-line bg-surface px-2.5 py-2"
            >
              <Icon name="user" className="h-3.5 w-3.5 shrink-0 text-ink-faint" />
              <span className="min-w-0 flex-1 truncate text-ui text-ink-soft">
                {m.user_email ?? m.user_id}
              </span>
              {m.is_owner ? (
                <Badge tone="brand">创建者</Badge>
              ) : isOwner ? (
                <IconButton
                  icon="x"
                  label={`移除成员 ${m.user_email ?? m.user_id}`}
                  size="xs"
                  variant="danger"
                  onClick={() => removeMut.mutate(m.user_id)}
                />
              ) : (
                <Badge tone="slate">成员</Badge>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* 添加器：仅 owner 可见（成员管理是 owner 特权） */}
      {isOwner && (
        <div className="flex items-center gap-2">
          <Select
            value={picked}
            onChange={(e) => setPicked(e.target.value)}
            controlSize="sm"
            aria-label="选择要添加的成员"
            wrapperClassName="flex-1"
          >
            <option value="">添加成员…</option>
            {candidates.map((u) => (
              <option key={u.id} value={u.id}>
                {u.email}
              </option>
            ))}
          </Select>
          <Button
            variant="secondary"
            size="sm"
            disabled={!picked || addMut.isPending}
            loading={addMut.isPending}
            onClick={() => picked && addMut.mutate(picked)}
          >
            添加
          </Button>
        </div>
      )}

      {!isOwner && <p className="text-xs text-ink-faint">仅项目创建者可以添加或移除成员。</p>}

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}
    </div>
  )
}
