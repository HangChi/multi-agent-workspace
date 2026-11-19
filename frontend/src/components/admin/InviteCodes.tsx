/**
 * 邀请码管理（015）：管理员在这里发码、看用量、吊销。
 *
 * 为什么需要它：邀请码此前只有消费端，默认码用尽后唯一的续发方式是用
 * SQL 往 invite_codes 里插一行。实测撞上过一次（10/10 用尽 → 注册 400，
 * 而界面上没有任何地方能改）。
 *
 * 交互取舍：
 * - 发出来的码**立即高亮并给复制按钮**——这是这个页面唯一的目的，不该让人
 *   在表格里找；
 * - 吊销走 useConfirm（破坏性、不可撤销）；
 * - 状态四种分开显示：用尽 / 过期 / 已吊销 / 可用。前三种的处置动作不同，
 *   合并成一句「不可用」等于逼人去猜。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { extractError } from '../../api/client'
import {
  createInviteCode,
  createInviteCodesBatch,
  fetchInviteCodes,
  revokeInviteCode,
} from '../../api/inviteCodes'
import type { InviteCodeItem, InviteRole, InviteState } from '../../api/inviteCodes'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { EmptyState } from '../ui/EmptyState'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Input, Select } from '../ui/Field'
import { Icon } from '../ui/Icons'
import { Skeleton } from '../ui/Skeleton'
import { useConfirm } from '../ui/useConfirm'
import { useToast } from '../ui/useToast'

const STATE_META: Record<
  InviteState,
  { label: string; tone: 'emerald' | 'amber' | 'rose' | 'slate' }
> = {
  active: { label: '可用', tone: 'emerald' },
  exhausted: { label: '已用尽', tone: 'amber' },
  expired: { label: '已过期', tone: 'slate' },
  revoked: { label: '已吊销', tone: 'rose' },
}

function formatDate(iso: string | null): string {
  if (!iso) return '永不过期'
  return new Date(iso).toLocaleDateString('zh-CN')
}

export function InviteCodes() {
  const queryClient = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()

  const [role, setRole] = useState<InviteRole>('member')
  const [maxUses, setMaxUses] = useState('10')
  const [expiresDays, setExpiresDays] = useState('')
  const [freshCodes, setFreshCodes] = useState<InviteCodeItem[]>([])
  const [count, setCount] = useState('1')
  const [error, setError] = useState<unknown>(null)

  const {
    data,
    isLoading,
    isError,
    error: loadError,
    refetch,
  } = useQuery({
    queryKey: ['admin', 'invite-codes'],
    queryFn: fetchInviteCodes,
  })

  const createMutation = useMutation({
    // 1 张走单发，多张走批量：两个接口的响应形状不同，本地归一成数组
    mutationFn: async (payload: {
      role: InviteRole
      max_uses: number
      expires_in_days: number | null
      count: number
    }) => {
      if (payload.count <= 1) {
        const item = await createInviteCode({
          role: payload.role,
          max_uses: payload.max_uses,
          expires_in_days: payload.expires_in_days,
        })
        return [item]
      }
      const list = await createInviteCodesBatch({
        role: payload.role,
        max_uses: payload.max_uses,
        expires_in_days: payload.expires_in_days,
        count: payload.count,
      })
      return list.items
    },
    onSuccess: (items) => {
      setFreshCodes(items)
      setError(null)
      toast.success(items.length > 1 ? `已生成 ${items.length} 张邀请码` : '邀请码已生成')
      void queryClient.invalidateQueries({ queryKey: ['admin', 'invite-codes'] })
    },
    onError: (err) => setError(err),
  })

  const revokeMutation = useMutation({
    mutationFn: revokeInviteCode,
    onSuccess: () => {
      toast.success('邀请码已吊销')
      void queryClient.invalidateQueries({ queryKey: ['admin', 'invite-codes'] })
    },
    onError: (err) => setError(err),
  })

  function submit() {
    const uses = Number(maxUses)
    if (!Number.isFinite(uses) || uses < 1) {
      toast.error('可用次数至少为 1')
      return
    }
    const days = expiresDays.trim() === '' ? null : Number(expiresDays)
    if (days !== null && (!Number.isFinite(days) || days < 1)) {
      toast.error('过期天数需 ≥1，留空表示永不过期')
      return
    }
    const n = Number(count)
    if (!Number.isFinite(n) || n < 1 || n > 20) {
      toast.error('一次最多生成 20 张')
      return
    }
    createMutation.mutate({ role, max_uses: uses, expires_in_days: days, count: n })
  }

  async function copy(code: string) {
    try {
      await navigator.clipboard.writeText(code)
      toast.success('邀请码已复制')
    } catch {
      // 剪贴板在非安全上下文会被拒——退化成让用户自己选中
      toast.error('复制失败，请手动选中复制')
    }
  }

  return (
    <Card>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-ink">邀请码</h2>
        <span className="text-xs text-ink-faint">成员凭码注册；码用尽或泄露时吊销后重发</span>
      </div>
      {error ? <ErrorBanner error={error} onDismiss={() => setError(null)} /> : null}

      {/* 发放 */}
      <div className="flex flex-wrap items-end gap-3 border-b border-line pb-4">
        <Select
          label="角色"
          value={role}
          onChange={(e) => setRole(e.target.value as InviteRole)}
          wrapperClassName="w-full sm:w-36"
        >
          <option value="member">成员（可建需求/Agent）</option>
          <option value="viewer">只读（仅查看）</option>
        </Select>
        <Input
          label="可用次数"
          type="number"
          min={1}
          max={1000}
          value={maxUses}
          onChange={(e) => setMaxUses(e.target.value)}
          hint="上限 1000"
          wrapperClassName="w-full sm:w-28"
        />
        <Input
          label="有效天数"
          type="number"
          min={1}
          max={365}
          value={expiresDays}
          onChange={(e) => setExpiresDays(e.target.value)}
          placeholder="留空=永久"
          wrapperClassName="w-full sm:w-28"
        />
        <Input
          label="数量"
          type="number"
          min={1}
          max={20}
          value={count}
          onChange={(e) => setCount(e.target.value)}
          hint="一次最多 20 张"
          wrapperClassName="w-full sm:w-24"
        />
        <Button
          variant="primary"
          leftIcon="plus"
          loading={createMutation.isPending}
          onClick={submit}
        >
          生成邀请码
        </Button>
      </div>

      {freshCodes.length > 0 ? (
        <div className="mt-4 rounded-lg border border-brand-200 bg-brand-50/60 px-4 py-3 dark:border-brand-500/30 dark:bg-brand-500/10">
          <div className="flex flex-wrap items-center gap-2">
            <Icon name="key" className="h-4 w-4 text-accent" />
            <span className="text-xs text-ink-soft">
              刚生成 {freshCodes.length} 张（高亮只保留到本次分发，如需再查请看下表）
            </span>
            <Button
              size="sm"
              variant="secondary"
              leftIcon="copy"
              className="ml-auto"
              onClick={() => copy(freshCodes.map((item) => item.code).join('\n'))}
            >
              全部复制
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setFreshCodes([])}>
              收起
            </Button>
          </div>
          <ul className="mt-2 flex flex-wrap gap-2">
            {freshCodes.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => copy(item.code)}
                  className="rounded bg-surface px-2 py-1 font-mono text-sm tracking-wider text-ink ring-1 ring-brand-200 transition-colors hover:ring-brand-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 dark:ring-brand-500/30"
                  title="点击复制"
                >
                  {item.code}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* 用量摘要：一眼看出「还有几张能用」，不用自己数表 */}
      {data && data.items.length > 0 ? (
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-soft">
          <span>
            共 <span className="font-semibold tabular-nums text-ink">{data.total}</span> 张
          </span>
          <span>
            可用{' '}
            <span className="font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
              {data.items.filter((i) => i.state === 'active').length}
            </span>
          </span>
          <span>
            已用尽{' '}
            <span className="tabular-nums">
              {data.items.filter((i) => i.state === 'exhausted').length}
            </span>
          </span>
          <span>
            已过期{' '}
            <span className="tabular-nums">
              {data.items.filter((i) => i.state === 'expired').length}
            </span>
          </span>
          <span>
            已吊销{' '}
            <span className="tabular-nums">
              {data.items.filter((i) => i.state === 'revoked').length}
            </span>
          </span>
          <span>
            累计用码注册{' '}
            <span className="font-semibold tabular-nums text-ink">
              {data.items.reduce((sum, i) => sum + i.used_count, 0)}
            </span>{' '}
            人
          </span>
        </div>
      ) : null}

      {/* 列表 */}
      <div className="mt-4">
        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : isError ? (
          <EmptyState
            icon="alert-triangle"
            title="加载失败"
            description={extractError(loadError)}
            action={
              <Button variant="secondary" leftIcon="refresh" onClick={() => void refetch()}>
                重试
              </Button>
            }
          />
        ) : !data || data.items.length === 0 ? (
          <EmptyState
            title="还没有邀请码"
            description="生成一张发给同事，对方在注册页填入即可加入。"
            bordered={false}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-ink-faint">
                  <th scope="col" className="py-2 pr-4 font-medium">
                    邀请码
                  </th>
                  <th scope="col" className="py-2 pr-4 font-medium">
                    角色
                  </th>
                  <th scope="col" className="py-2 pr-4 font-medium">
                    用量
                  </th>
                  <th scope="col" className="py-2 pr-4 font-medium">
                    有效期至
                  </th>
                  <th scope="col" className="py-2 pr-4 font-medium">
                    状态
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => {
                  const meta = STATE_META[item.state]
                  return (
                    <tr key={item.id} className="border-b border-line/60 last:border-0">
                      <td className="py-2 pr-4">
                        <code className="font-mono text-xs tracking-wider text-ink">
                          {item.code}
                        </code>
                      </td>
                      <td className="py-2 pr-4 text-ink-soft">
                        {item.role === 'member' ? '成员' : '只读'}
                      </td>
                      <td className="py-2 pr-4 tabular-nums text-ink-soft">
                        {item.used_count} / {item.max_uses}
                      </td>
                      <td className="py-2 pr-4 text-ink-soft">{formatDate(item.expires_at)}</td>
                      <td className="py-2 pr-4">
                        <Badge tone={meta.tone} dot>
                          {meta.label}
                        </Badge>
                      </td>
                      <td className="py-2 text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            leftIcon="copy"
                            onClick={() => copy(item.code)}
                          >
                            复制
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            tone="danger"
                            disabled={item.state === 'revoked'}
                            loading={
                              revokeMutation.isPending && revokeMutation.variables === item.id
                            }
                            onClick={async () => {
                              const ok = await confirm({
                                title: '吊销这张邀请码？',
                                description: `「${item.code}」将立即失效，已注册的人不受影响。此操作不可撤销。`,
                                tone: 'danger',
                                confirmText: '吊销',
                              })
                              if (ok) revokeMutation.mutate(item.id)
                            }}
                          >
                            吊销
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  )
}
