/** 移交项目所有权（017）。
 *
 * 为什么是独立弹层而不是在成员区就地操作：
 * 1. 移交是不可撤销的权限变更，需要「选人 + 说明后果 + 危险按钮」三件套；
 * 2. 同名冲突的错误消息必须完整展示（那是唯一告诉用户「怎么修」的地方）；
 * 3. 成员区对 owner 已是「加人/踢人」的高频区，把移交混进去容易误点。
 *
 * 目标只能是**现有成员**（后端 409 也是这么判的）：移交是接力不是空降，
 * 想交给陌生人请先加为成员（research R-02）。
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../../api/client'
import { fetchProjectMembers } from '../../api/endpoints'
import { transferProject } from '../../api/projects'
import type { Project } from '../../api/projects'
import { useAuth } from '../../hooks/useAuth'
import { Button } from '../ui/Button'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Select } from '../ui/Field'
import { Modal } from '../ui/Modal'
import { useToast } from '../ui/useToast'

interface Props {
  project: Project
  open: boolean
  onClose: () => void
}

export function ProjectTransferDialog({ project, open, onClose }: Props) {
  const qc = useQueryClient()
  const toast = useToast()
  const { user } = useAuth()
  const [target, setTarget] = useState('')
  const [keepAsMember, setKeepAsMember] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const { data } = useQuery({
    queryKey: ['projectMembers', project.id],
    queryFn: () => fetchProjectMembers(project.id),
    // 只有打开时才查：列表页可能同时渲染几十张卡片
    enabled: open,
  })
  // 排除自己：后端对自移交回 409，前端不该给一条走不通的路
  const candidates = (data?.items ?? []).filter((m) => m.user_id !== user?.id)

  const mut = useMutation({
    mutationFn: () =>
      transferProject(project.id, {
        new_owner_id: target,
        keep_as_member: keepAsMember,
      }),
    onSuccess: () => {
      // 项目列表（「我的/共享的」徽章）与成员列表都要刷：同一份数据被两处读
      void qc.invalidateQueries({ queryKey: ['projects'] })
      void qc.invalidateQueries({ queryKey: ['projectMembers', project.id] })
      toast.success('已移交项目所有权')
      handleClose()
    },
    // 错误消息**原样**展示：同名冲突的解释（带项目名）只在后端 message 里
    onError: (err) => setError(extractError(err)),
  })

  function handleClose() {
    setTarget('')
    setKeepAsMember(true)
    setError(null)
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="移交项目所有权"
      description={
        <>
          「{project.name}」移交后，<strong className="font-semibold text-ink">对方将获得全部管理权限</strong>
          （改名、管成员、归档、删除）。此操作不可撤销，如需还原要由对方再移交回来。
        </>
      }
      footer={
        <>
          <Button variant="ghost" onClick={handleClose} disabled={mut.isPending}>
            取消
          </Button>
          <Button
            tone="danger"
            loading={mut.isPending}
            disabled={!target}
            onClick={() => {
              setError(null)
              mut.mutate()
            }}
          >
            确认移交
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

        <Select
          label="移交给谁"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          hint={
            candidates.length === 0
              ? '这个项目还没有其他成员——先在成员区把人加进来，再回来移交'
              : '只能移交给项目现有成员'
          }
        >
          <option value="">请选择成员</option>
          {candidates.map((m) => (
            <option key={m.user_id} value={m.user_id}>
              {m.user_email ?? m.user_id}
            </option>
          ))}
        </Select>

        <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-line-subtle bg-surface-subtle/60 p-3">
          <input
            type="checkbox"
            checked={keepAsMember}
            onChange={(e) => setKeepAsMember(e.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-brand-600"
          />
          <span className="text-ui text-ink-soft">
            移交后我仍保留为成员
            <span className="mt-0.5 block text-xs text-ink-faint">
              可继续查看和协作这个项目；取消勾选则完全退出（之后访问会提示项目不存在）。
            </span>
          </span>
        </label>
      </div>
    </Modal>
  )
}
