/**
 * 邀请码申请（027）：成员提申请，admin 审批。
 *
 * 一页两用：成员看到「我要几个码」的表单和自己的申请记录；admin 额外看到待审批
 * 队列。不拆两个页面，是因为申请与审批本来就是一个来回——拆开两边都要切页面。
 *
 * 批准即发码（走 022 的发码路径，部门配额照旧生效），所以「批了没码」这种状态
 * 不存在：配额不足时后端直接 409，申请留在待审批里。
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
import {
  approveInviteRequest,
  createInviteRequest,
  fetchInviteRequests,
  rejectInviteRequest,
} from '../api/endpoints'
import type { InviteRequest } from '../api/endpoints'
import { useAuth } from '../hooks/useAuth'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Input, Select } from '../components/ui/Field'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { useConfirm } from '../components/ui/useConfirm'
import { useToast } from '../components/ui/useToast'

const STATUS_META = {
  pending: { label: '待审批', tone: 'amber' },
  approved: { label: '已通过', tone: 'emerald' },
  rejected: { label: '未通过', tone: 'rose' },
} as const

export function InviteRequestsPage() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const isAdmin = user?.role === 'admin'

  const [role, setRole] = useState<'member' | 'viewer'>('member')
  const [count, setCount] = useState('1')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const { data: rows = [], refetch } = useQuery({
    queryKey: ['inviteRequests', isAdmin ? 'admin' : 'mine'],
    queryFn: () => fetchInviteRequests(),
  })

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['inviteRequests'] })
  }

  const createMut = useMutation({
    mutationFn: () => createInviteRequest({ role, count: Number(count), reason }),
    onSuccess: () => {
      setReason('')
      setCount('1')
      toast.success('已提交申请，等待管理员审批')
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  const approveMut = useMutation({
    mutationFn: (id: string) => approveInviteRequest(id),
    onSuccess: (row) => {
      toast.success(`已批准，生成 ${row.issued_codes?.split('\n').length ?? 0} 个邀请码`)
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  const rejectMut = useMutation({
    mutationFn: (id: string) => rejectInviteRequest(id, '管理员拒绝'),
    onSuccess: () => {
      toast.success('已拒绝该申请')
      invalidate()
    },
    onError: (err) => setError(extractError(err)),
  })

  async function onReject(row: InviteRequest) {
    const ok = await confirm({
      title: '拒绝这条申请？',
      description: `申请人会收到通知（申请理由：${row.reason}）。`,
      tone: 'warning',
      confirmText: '拒绝',
    })
    if (ok) rejectMut.mutate(row.id)
  }

  const pending = rows.filter((r) => r.status === 'pending')

  return (
    <Page>
      <PageHeader
        title="邀请码申请"
        description={
          isAdmin
            ? '成员提交的邀请码申请。批准即发码（部门月度配额仍然生效）。'
            : '需要给新同事开账号时在这里申请；管理员批准后邀请码会直接出现在这条记录里。'
        }
        actions={
          <Button variant="ghost" size="sm" leftIcon="refresh" onClick={() => void refetch()}>
            刷新
          </Button>
        }
      />

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} className="mt-4" />}

      <Card className="mt-4" padding="md">
        <p className="mb-3 text-ui font-medium text-ink-soft">提交申请</p>
        <div className="flex flex-wrap items-end gap-2">
          <Select
            label="角色"
            value={role}
            onChange={(e) => setRole(e.target.value as 'member' | 'viewer')}
            wrapperClassName="w-40"
          >
            <option value="member">成员</option>
            <option value="viewer">只读</option>
          </Select>
          <Input
            label="需要几个"
            type="number"
            min={1}
            max={20}
            value={count}
            onChange={(e) => setCount(e.target.value)}
            wrapperClassName="w-28"
          />
          <Input
            label="申请理由"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="例如：新同事下周入职"
            wrapperClassName="min-w-64 flex-1"
          />
          <Button
            loading={createMut.isPending}
            disabled={!reason.trim()}
            onClick={() => createMut.mutate()}
          >
            提交
          </Button>
        </div>
      </Card>

      <div className="mt-4 space-y-2">
        <p className="text-ui font-medium text-ink-soft">
          {isAdmin ? '申请队列' : '我的申请'}
          <span className="ml-1.5 text-xs font-normal text-ink-faint">
            {rows.length} 条{isAdmin && pending.length > 0 ? ` · ${pending.length} 条待审批` : ''}
          </span>
        </p>

        {rows.length === 0 ? (
          <EmptyState
            icon="key"
            title="还没有申请"
            description={
              isAdmin
                ? '成员提交申请后会出现在这里。'
                : '需要邀请码时在上方提交一条申请，管理员批准后码会显示在这里。'
            }
          />
        ) : (
          <ul className="space-y-2">
            {rows.map((row) => (
              <li key={row.id}>
                <Card padding="sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={STATUS_META[row.status].tone}>
                      {STATUS_META[row.status].label}
                    </Badge>
                    <span className="text-ui text-ink-soft">
                      {row.role === 'member' ? '成员' : '只读'} × {row.count}
                    </span>
                    {isAdmin && row.requester_email && (
                      <span className="text-xs text-ink-faint">来自 {row.requester_email}</span>
                    )}
                    {row.department_name && (
                      <span className="text-xs text-ink-faint">{row.department_name}</span>
                    )}
                    <span className="min-w-0 flex-1 truncate text-xs text-ink-muted">
                      {row.reason}
                    </span>
                    {isAdmin && row.status === 'pending' && (
                      <span className="flex items-center gap-1">
                        <Button
                          size="sm"
                          loading={approveMut.isPending}
                          onClick={() => approveMut.mutate(row.id)}
                        >
                          批准并生成
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          tone="danger"
                          onClick={() => void onReject(row)}
                        >
                          拒绝
                        </Button>
                      </span>
                    )}
                  </div>
                  {row.issued_codes && (
                    <pre className="mt-2 select-all whitespace-pre-wrap rounded-md bg-surface-subtle px-2 py-1.5 font-mono text-xs text-ink-soft">
                      {row.issued_codes}
                    </pre>
                  )}
                </Card>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Page>
  )
}
