import { apiClient } from './client'

// ---------- 外部通知通道（018）----------

export type ChannelKind = 'webhook'

/** 一条外部通道。secret 永不回显，只有 secret_configured。 */
export interface NotificationChannel {
  id: string
  kind: ChannelKind
  url: string
  /** 空数组 = 订阅全部事件 */
  event_types: string[]
  enabled: boolean
  created_at: string
  updated_at: string
  secret_configured: boolean
}

export interface NotificationDelivery {
  id: string
  channel_id: string
  notification_id: string | null
  event_type: string
  status: 'pending' | 'succeeded' | 'failed'
  attempts: number
  last_error: string | null
  last_attempt_at: string | null
  created_at: string
}

export interface ChannelTestResult {
  ok: boolean
  status_code: number | null
  error: string | null
}

export interface EventType {
  value: string
  label: string
}

export async function fetchChannels(): Promise<NotificationChannel[]> {
  const { data } = await apiClient.get<{ items: NotificationChannel[]; total: number }>(
    '/notification-channels'
  )
  return data.items
}

export async function createChannel(payload: {
  url: string
  event_types: string[]
  enabled?: boolean
}): Promise<NotificationChannel> {
  const { data } = await apiClient.post<NotificationChannel>('/notification-channels', payload)
  return data
}

export async function updateChannel(
  id: string,
  payload: { url?: string; event_types?: string[]; enabled?: boolean }
): Promise<NotificationChannel> {
  const { data } = await apiClient.patch<NotificationChannel>(
    `/notification-channels/${id}`,
    payload
  )
  return data
}

export async function deleteChannel(id: string): Promise<void> {
  await apiClient.delete(`/notification-channels/${id}`)
}

/** 同步发一条测试事件（后端最多等 8 秒）。 */
export async function testChannel(id: string): Promise<ChannelTestResult> {
  const { data } = await apiClient.post<ChannelTestResult>(`/notification-channels/${id}/test`)
  return data
}

/** 重置签名密钥（旧密钥立即失效）。 */
export async function resetChannelSecret(id: string): Promise<NotificationChannel> {
  const { data } = await apiClient.post<NotificationChannel>(
    `/notification-channels/${id}/reset-secret`
  )
  return data
}

export async function fetchChannelDeliveries(
  id: string,
  limit = 10
): Promise<NotificationDelivery[]> {
  const { data } = await apiClient.get<{ items: NotificationDelivery[]; total: number }>(
    `/notification-channels/${id}/deliveries`,
    { params: { limit } }
  )
  return data.items
}

export async function fetchEventTypes(): Promise<EventType[]> {
  const { data } = await apiClient.get<{ items: EventType[] }>('/notification-channels/event-types')
  return data.items
}
