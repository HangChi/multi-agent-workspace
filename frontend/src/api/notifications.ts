import { apiClient } from './client'

// ---------- 站内通知（007 US5）----------

export type NotificationType =
  | 'request_dispatched'
  | 'request_accepted'
  | 'request_rejected'
  | 'request_reassigned'
  | 'run_completed'
  // 009：前置终态 → 后续方「可以启动了」
  | 'dependency_ready'

export interface Notification {
  id: string
  type: NotificationType
  request_id: string | null
  /** 标题快照（需求标题，事后可改但通知不改）。 */
  title: string
  payload: Record<string, unknown>
  read_at: string | null
  created_at: string
}

export interface NotificationList {
  items: Notification[]
  total: number
}

/** 我的通知（倒序；unread_only 只取未读）。 */
export async function fetchNotifications(params?: {
  unread_only?: boolean
  page?: number
  page_size?: number
}): Promise<NotificationList> {
  const { data } = await apiClient.get<NotificationList>('/notifications', { params })
  return data
}

/** 未读数（顶栏铃铛数据源）。 */
export async function fetchUnreadCount(): Promise<number> {
  const { data } = await apiClient.get<{ unread: number }>('/notifications/unread-count')
  return data.unread
}

/** 批量已读：ids 缺省 = 全部已读；返回生效行数。 */
export async function markNotificationsRead(ids?: string[]): Promise<number> {
  const { data } = await apiClient.post<{ updated: number }>('/notifications/read', {
    ids,
  })
  return data.updated
}
