/**
 * 通知铃铛（007 T033）：顶栏未读入口 + 通知中心面板。
 *
 * 设计取舍：
 * - 未读数走 TanStack Query 轮询（refetchInterval ≤30s，SC-007），不建用户级
 *   WS——为一个小红点引入长连接的在线状态/多标签去发/重连补发，成本远超收益
 *   （research R-06）；
 * - 面板用 Dropdown（项目现成组件）：点外面关、Esc 关、焦点归还，不用重复造。
 *   注意 Dropdown 的键盘导航只认 role="menuitem"，所以面板内的可点项
 *   （通知 / 全部已读）都带该角色——否则读屏与 ↑↓ 导航全部失效；
 * - 列表与未读计数同周期轮询：否则徽标涨了、面板里看不到，用户以为功能坏了；
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
import { extractError } from '../../api/client'
import { Badge } from '../ui/Badge'
import { Dropdown, DropdownSeparator } from '../ui/Dropdown'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Icon } from '../ui/Icons'
import { Skeleton } from '../ui/Skeleton'
import { cn } from '../ui/cn'
import { useToast } from '../ui/useToast'

const FALLBACK_META = { label: '通知', icon: 'inbox' as const }

const TYPE_META: Record<
  NotificationType,
  {
    label: string
    icon: 'send' | 'check-circle' | 'x-circle' | 'users' | 'play' | 'clock' | 'inbox'
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

/** 后端新增通知类型时不能把整个铃铛搞崩（TYPE_META 直索引会 TypeError） */
function metaFor(type: NotificationType) {
  return TYPE_META[type] ?? FALLBACK_META
}

function timeAgo(iso: string): string {
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) return '时间未知'
  const diff = Date.now() - time
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
  const meta = metaFor(notification.type)
  return (
    <button
      type="button"
      // Dropdown 的键盘导航（↑↓/Home/End）只收集 role=menuitem；
      // 缺这个角色，通知面板对键盘用户就是一片死地
      role="menuitem"
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
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500"
              aria-label="未读"
            />
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
  const toast = useToast()
  const [marking, setMarking] = useState(false)

  // keepPreviousData：轮询间隙不让徽标闪回 0（误判「通知没了」）
  const { data: unread = 0, isLoading: unreadLoading } = useQuery({
    queryKey: ['notifications', 'unread-count'],
    queryFn: fetchUnreadCount,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  })

  // 列表同周期轮询：只轮询未读计数的话，徽标与面板内容会持续矛盾
  const {
    data: list,
    isLoading: listLoading,
    isError: listError,
    refetch: refetchList,
  } = useQuery({
    queryKey: ['notifications', 'list'],
    queryFn: () => fetchNotifications({ page_size: 20 }),
    refetchInterval: 30_000,
  })

  const items = list?.items ?? []

  const markAll = async () => {
    setMarking(true)
    try {
      await markNotificationsRead()
      await qc.invalidateQueries({ queryKey: ['notifications'] })
    } catch (err) {
      // 失败不能静默：用户点了「全部已读」，徽标却没动，必须有交代
      toast.error('全部已读失败', extractError(err))
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
      ariaLabel={unread > 0 ? `通知，${unread} 条未读` : '通知'}
      panelClassName="w-80 max-w-[calc(100vw-1.5rem)]"
      triggerClassName="relative h-9 w-9 justify-center hover:bg-surface-subtle"
      trigger={
        <>
          <Icon name="inbox" className="h-4 w-4" />
          {unread > 0 && (
            <span
              // role=status：新通知到达时读屏能播报（aria-label 放 generic
              // 角色上多数读屏忽略，不可访问名称改由 trigger 承担）
              role="status"
              className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand-600 px-1 text-micro font-semibold tabular-nums text-white"
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
            role="menuitem"
            onClick={() => void markAll()}
            disabled={marking}
            className="rounded px-1.5 py-0.5 text-xs font-medium text-accent transition-colors hover:bg-surface-subtle disabled:text-ink-faint"
          >
            {marking ? '处理中…' : '全部已读'}
          </button>
        )}
      </div>
      <DropdownSeparator />

      {/* 三态分离：加载中 ≠ 错误 ≠ 真的没有通知。
          此前的实现把失败渲染成「还没有通知」，把故障伪装成空数据。 */}
      {listLoading && items.length === 0 ? (
        <div className="space-y-2.5 px-2.5 py-3" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex items-start gap-2.5">
              <Skeleton className="h-6 w-6 shrink-0 rounded-md" />
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-3 w-16" />
                <Skeleton className="h-3 w-full" />
              </div>
            </div>
          ))}
        </div>
      ) : listError ? (
        <div className="px-2.5 py-2">
          <ErrorBanner error={listError} retry={() => void refetchList()} />
        </div>
      ) : items.length === 0 ? (
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

      {/* 未读计数拉取中的占位：避免铃铛在 0 与真实值之间跳 */}
      {unreadLoading && unread === 0 && (
        <span className="sr-only" role="status">
          正在获取未读通知数…
        </span>
      )}
    </Dropdown>
  )
}
