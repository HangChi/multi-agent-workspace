import { apiClient } from './client'

// ---------- 审计日志（T079，Phase 8）----------

export interface AuditLogItem {
  id: string
  user_id: string
  action: string
  target_type: string | null
  target_id: string | null
  detail: Record<string, unknown> | null
  created_at: string
}

export interface AuditLogList {
  items: AuditLogItem[]
  total: number
}

export interface AuditLogQuery {
  user_id?: string
  action?: string
  page?: number
  page_size?: number
}

/** 审计日志列表（admin 专属，支持筛选与分页）。 */
export async function fetchAuditLogs(query: AuditLogQuery = {}): Promise<AuditLogList> {
  const params = new URLSearchParams()
  if (query.user_id) params.set('user_id', query.user_id)
  if (query.action) params.set('action', query.action)
  if (query.page) params.set('page', String(query.page))
  if (query.page_size) params.set('page_size', String(query.page_size))
  const qs = params.toString()
  const { data } = await apiClient.get<AuditLogList>(`/audit-logs${qs ? `?${qs}` : ''}`)
  return data
}
