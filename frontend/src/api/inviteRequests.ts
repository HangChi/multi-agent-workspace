import { apiClient } from './client'

// ---------- 邀请码申请（027）----------

export type InviteRequestStatus = 'pending' | 'approved' | 'rejected'

export interface InviteRequest {
  id: string
  requester_id: string
  requester_email: string | null
  department_id: string | null
  department_name: string | null
  role: 'member' | 'viewer'
  count: number
  reason: string
  status: InviteRequestStatus
  /** 批准后生成的码（换行分隔），申请人直接复制 */
  issued_codes: string | null
  decided_at: string | null
  created_at: string
}

export async function fetchInviteRequests(status?: InviteRequestStatus): Promise<InviteRequest[]> {
  const { data } = await apiClient.get<{ items: InviteRequest[]; total: number }>(
    '/invite-requests',
    { params: status ? { status } : undefined }
  )
  return data.items
}

export async function createInviteRequest(payload: {
  role: 'member' | 'viewer'
  count: number
  reason: string
  department_id?: string | null
}): Promise<InviteRequest> {
  const { data } = await apiClient.post<InviteRequest>('/invite-requests', payload)
  return data
}

export async function approveInviteRequest(id: string): Promise<InviteRequest> {
  const { data } = await apiClient.post<InviteRequest>(`/invite-requests/${id}/approve`)
  return data
}

export async function rejectInviteRequest(id: string, reason?: string): Promise<InviteRequest> {
  const { data } = await apiClient.post<InviteRequest>(`/invite-requests/${id}/reject`, {
    reason: reason ?? null,
  })
  return data
}
