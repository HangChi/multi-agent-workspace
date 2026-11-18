/** 通知铃铛（007 T033）：顶栏未读入口 + 通知中心面板。
 *
 * 设计取舍：
 * - 未读数走 TanStack Query 轮询（refetchInterval ≤30s，SC-007），不建用户级
 *   WS——为一个小红点引入长连接的在线状态/多标签去发/重连补发，成本远超收益
 *   （research R-06）；
 * - 面板用 Dropdown（项目现成组件）：点外面关、Esc 关、焦点归还，不用重复造；
 * - 「全部已读」是唯一写操作，命中后同时 invalidate 列表与未读计数；
 * - 每条通知可点进对应需求（request_id 非空时）——通知是入口不是终点。
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import {
  fetchNotifications,
  fetchUnreadCount,
  markNotificationsRead,
  Notification,
  NotificationType,
} from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Dropdown, DropdownSeparator } from '../ui/Dropdown'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

const TYPE_META: Record<
  NotificationType,
  {
    label: string
    icon: 'send' | 'check-circle' | 'x-circle' | 'users' | 'play' | 'clock'
  }
> = {
  request_dispatched: { label: '新分派', icon: 'send' },
  request_accepted: { label: '已接收', icon: 'check-circle' },
  request_rejected: { label: '被退回', icon: 'x-circle' },
  request_reassigned: { label: '被转交', icon: 'users' },
  run_completed: { label: '执行完成', icon: 'play' },
  // 009：前置终态 → 可以启动了（措辞按 payload.dependency_status 三分，
  // 前端在通知项里渲染，这里只给图标与类型标签）
  dependency_ready: { label: '前置就绪', icon: 'clock' },
  // 010：成员变动（两种共用一条 meta，文案按 type 二分）
  project_member_added: { label: '成员变动', icon: 'users' },
  project_member_removed: { label: '成员变动', icon: 'users' },
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

function NotificationItem({
  notification,
  onOpen,
}: {
  notification: Notification
  onOpen: () => void
}) {
  const meta = TYPE_META[notification.type]
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        'flex w-full items-start gap-2.5 px-2.5 py-2 text-left transition-colors hover:bg-surface-subtle',
        'focus-visible:outline-none focus-visible:bg-surface-subtle'
      )}
    >
      <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-muted text-ink-muted">
        <Icon name={meta.icon} className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <Badge tone="slate">{meta.label}</Badge>
          {notification.read_at === null && (
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" aria-label="未读" />
          )}
        </span>
        <span className="mt-1 block truncate text-ui text-ink-soft">{notification.title}</span>
        <span className="mt-0.5 block text-micro text-ink-faint">
          {timeAgo(notification.created_at)}
        </span>
      </span>
    </button>
  )
}

export function NotificationBell() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [marking, setMarking] = useState(false)

  const { data: unread = 0 } = useQuery({
    queryKey: ['notifications', 'unread-count'],
    queryFn: fetchUnreadCount,
    refetchInterval: 30_000,
  })

  const { data: list } = useQuery({
    queryKey: ['notifications', 'list'],
    queryFn: () => fetchNotifications({ page_size: 20 }),
  })

  const items = list?.items ?? []

  const markAll = async () => {
    setMarking(true)
    try {
      await markNotificationsRead()
      await qc.invalidateQueries({ queryKey: ['notifications'] })
    } finally {
      setMarking(false)
    }
  }

  const openNotification = (n: Notification) => {
    if (n.request_id) {
      navigate(`/?request=${n.request_id}`)
    }
  }

  return (
    <Dropdown
      ariaLabel="通知"
      panelClassName="w-80"
      triggerClassName="relative h-9 w-9 justify-center hover:bg-surface-subtle"
      trigger={
        <>
          <Icon name="inbox" className="h-4 w-4" />
          {unread > 0 && (
            <span
              className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-600 px-1 text-micro font-semibold tabular-nums text-white"
              aria-label={`${unread} 条未读`}
            >
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </>
      }
    >
      <div className="flex items-center justify-between px-2.5 pb-1.5 pt-1">
        <p className="text-ui font-medium text-ink">通知</p>
        {unread > 0 && (
          <button
            type="button"
            onClick={() => void markAll()}
            disabled={marking}
            className="rounded px-1.5 py-0.5 text-xs font-medium text-accent transition-colors hover:bg-surface-subtle disabled:text-ink-faint"
          >
            {marking ? '处理中…' : '全部已读'}
          </button>
        )}
      </div>
      <DropdownSeparator />

      {items.length === 0 ? (
        <p className="px-2.5 py-6 text-center text-ui text-ink-faint">
          还没有通知——分派、接收、退回与执行完成都会出现在这里
        </p>
      ) : (
        <div className="max-h-80 overflow-y-auto py-1">
          {items.map((n) => (
            <NotificationItem key={n.id} notification={n} onOpen={() => openNotification(n)} />
          ))}
        </div>
      )}
    </Dropdown>
  )
}
