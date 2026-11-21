/**
 * 通知渠道设置页（018）：把站内通知接到团队已有的 IM 机器人 / 内部网关。
 *
 * 三个设计取舍：
 * 1. **每张卡片自带「最近投递」**：配好却没收到是最常见的问题，
 *    「最近一次成功/失败 + 尝试次数 + 错误」是唯一能自助排查的信息（US3）；
 * 2. **测试按钮同步等结果**：配置时要立刻知道通不通，排队的测试发出去失败
 *    用户也看不见（FR-009）；
 * 3. **事件全不选 = 全部事件**：这个语义在 UI 上必须写明，否则用户以为「没勾
 *    就是不订阅」，结果收到所有通知。
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { extractError } from '../api/client'
import {
  createChannel,
  deleteChannel,
  fetchChannelDeliveries,
  fetchChannels,
  fetchEventTypes,
  resetChannelSecret,
  testChannel,
  updateChannel,
} from '../api/endpoints'
import type { NotificationChannel } from '../api/endpoints'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Input } from '../components/ui/Field'
import { Modal } from '../components/ui/Modal'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { CardSkeleton } from '../components/ui/Skeleton'
import { useConfirm } from '../components/ui/useConfirm'
import { useToast } from '../components/ui/useToast'

const STATUS_TONE = {
  succeeded: 'emerald',
  failed: 'rose',
  pending: 'amber',
} as const

const STATUS_LABEL = {
  succeeded: '成功',
  failed: '失败',
  pending: '重试中',
} as const

export function NotificationChannelsPage() {
  const qc = useQueryClient()
  const toast = useToast()
  const confirm = useConfirm()
  const [showCreate, setShowCreate] = useState(false)
  const [pageError, setPageError] = useState<string | null>(null)

  const {
    data: channels = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({ queryKey: ['notificationChannels'], queryFn: fetchChannels })

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['notificationChannels'] })
  }

  const toggleMut = useMutation({
    mutationFn: (channel: NotificationChannel) =>
      updateChannel(channel.id, { enabled: !channel.enabled }),
    onSuccess: (channel) => {
      toast.success(channel.enabled ? '已启用通道' : '已停用通道')
      invalidate()
    },
    onError: (err) => setPageError(extractError(err)),
  })

  const testMut = useMutation({
    mutationFn: (id: string) => testChannel(id),
    onSuccess: (result) => {
      if (result.ok) {
        toast.success(`测试成功（HTTP ${result.status_code}）`)
      } else {
        toast.error(`测试失败：${result.error ?? '未知错误'}`)
      }
      invalidate()
    },
    onError: (err) => toast.error(extractError(err)),
  })

  const resetMut = useMutation({
    mutationFn: (id: string) => resetChannelSecret(id),
    onSuccess: () => {
      toast.success('已重置签名密钥（旧密钥立即失效）')
      invalidate()
    },
    onError: (err) => setPageError(extractError(err)),
  })

  const removeMut = useMutation({
    mutationFn: (id: string) => deleteChannel(id),
    onSuccess: () => {
      toast.success('已删除通道')
      invalidate()
    },
    onError: (err) => setPageError(extractError(err)),
  })

  async function onRemove(channel: NotificationChannel) {
    const ok = await confirm({
      title: '删除这个通知通道？',
      description: `之后的事件不会再推送到 ${channel.url}。已产生的投递记录会一并删除。`,
      tone: 'warning',
      confirmText: '删除',
    })
    if (ok) removeMut.mutate(channel.id)
  }

  return (
    <Page>
      <PageHeader
        title="通知渠道"
        description="把站内通知接到企业微信机器人、飞书机器人或内部网关。每条事件会带 HMAC 签名，接收端可验证来源。"
        actions={
          <Button leftIcon="plus" onClick={() => setShowCreate(true)}>
            新建通道
          </Button>
        }
      />

      {pageError && (
        <ErrorBanner error={pageError} onDismiss={() => setPageError(null)} className="mt-4" />
      )}

      {isLoading ? (
        <div className="mt-4 space-y-3">
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : isError ? (
        <div className="mt-4">
          <ErrorBanner error="加载通知通道失败" retry={() => void refetch()} />
        </div>
      ) : channels.length === 0 ? (
        <div className="mt-4">
          <EmptyState
            icon="plug"
            title="还没有通知渠道"
            description="配置一条 Webhook 后，派单、运行完成、依赖解锁等事件会实时推送到你的 IM 群或内部网关。"
            action={
              <Button leftIcon="plus" onClick={() => setShowCreate(true)}>
                新建第一条通道
              </Button>
            }
          />
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {channels.map((channel) => (
            <ChannelCard
              key={channel.id}
              channel={channel}
              testing={testMut.isPending && testMut.variables === channel.id}
              onToggle={() => toggleMut.mutate(channel)}
              onTest={() => testMut.mutate(channel.id)}
              onReset={() => resetMut.mutate(channel.id)}
              onRemove={() => void onRemove(channel)}
            />
          ))}
        </div>
      )}

      {showCreate && (
        <CreateChannelDialog
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            invalidate()
            setShowCreate(false)
          }}
        />
      )}
    </Page>
  )
}

function ChannelCard({
  channel,
  testing,
  onToggle,
  onTest,
  onReset,
  onRemove,
}: {
  channel: NotificationChannel
  testing: boolean
  onToggle: () => void
  onTest: () => void
  onReset: () => void
  onRemove: () => void
}) {
  const { data: deliveries = [] } = useQuery({
    queryKey: ['channelDeliveries', channel.id],
    queryFn: () => fetchChannelDeliveries(channel.id, 3),
  })
  const latest = deliveries[0]

  return (
    <Card padding="md">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={channel.enabled ? 'emerald' : 'slate'} dot>
              {channel.enabled ? '已启用' : '已停用'}
            </Badge>
            <Badge tone="sky">Webhook</Badge>
            <span className="truncate font-mono text-ui text-ink" title={channel.url}>
              {channel.url}
            </span>
          </div>
          <p className="mt-1 text-xs text-ink-faint">
            {channel.event_types.length === 0
              ? '订阅全部事件'
              : `订阅 ${channel.event_types.length} 类事件`}
            {' · '}
            签名密钥已配置（不显示，可重置）
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-1">
          <Button variant="ghost" size="sm" leftIcon="send" loading={testing} onClick={onTest}>
            发送测试
          </Button>
          <Button variant="ghost" size="sm" onClick={onToggle}>
            {channel.enabled ? '停用' : '启用'}
          </Button>
          <Button variant="ghost" size="sm" onClick={onReset}>
            重置密钥
          </Button>
          <Button variant="ghost" size="sm" tone="danger" leftIcon="trash" onClick={onRemove}>
            删除
          </Button>
        </div>
      </div>

      <div className="mt-3 border-t border-line-subtle pt-2 text-xs text-ink-muted">
        {latest ? (
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-ink-faint">最近投递</span>
            <Badge tone={STATUS_TONE[latest.status]}>{STATUS_LABEL[latest.status]}</Badge>
            <span className="font-mono">{latest.event_type}</span>
            <span className="text-ink-faint">尝试 {latest.attempts} 次</span>
            {latest.last_error && (
              <span className="truncate text-rose-600" title={latest.last_error}>
                {latest.last_error}
              </span>
            )}
          </span>
        ) : (
          <span className="text-ink-faint">还没有投递记录——事件发生时才会产生</span>
        )}
      </div>
    </Card>
  )
}

function CreateChannelDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: () => void
}) {
  const [url, setUrl] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const { data: eventTypes = [] } = useQuery({
    queryKey: ['notificationEventTypes'],
    queryFn: fetchEventTypes,
    staleTime: Infinity,
  })

  const mut = useMutation({
    mutationFn: () => createChannel({ url, event_types: selected }),
    onSuccess: onCreated,
    onError: (err) => setError(extractError(err)),
  })

  function toggleEvent(value: string) {
    setSelected((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]
    )
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="新建通知通道"
      description="填一个能接收 POST 的地址（企业微信/飞书机器人的 webhook URL 或你自己的网关）。"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={mut.isPending}>
            取消
          </Button>
          <Button
            loading={mut.isPending}
            disabled={url.trim().length < 8}
            onClick={() => {
              setError(null)
              mut.mutate()
            }}
          >
            创建
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

        <Input
          label="Webhook 地址"
          placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          hint="只支持公网 http/https；内网与保留地址（127.0.0.1、10.x、169.254.x 等）会被拒绝。"
        />

        <div>
          <p className="mb-2 text-ui font-medium text-ink-soft">订阅哪些事件</p>
          <p className="mb-2 text-xs text-ink-faint">
            全不勾 = 订阅全部事件（之后新增的事件类型也会自动收到）。
          </p>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {eventTypes.map((event) => (
              <label
                key={event.value}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-surface-subtle"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(event.value)}
                  onChange={() => toggleEvent(event.value)}
                  className="h-4 w-4 cursor-pointer accent-brand-600"
                />
                <span className="text-ui text-ink-soft">{event.label}</span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  )
}
