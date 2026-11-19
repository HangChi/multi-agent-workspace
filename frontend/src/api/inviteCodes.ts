import { apiClient } from './client'

/**
 * 邀请码管理（admin 专属）。
 *
 * 码值在列表里直接返回——管理员本来就要把它发给同事，不像密码那样需要
 * 二次查看；发错了/泄露了用吊销处理（state 会变成 revoked）。
 */

export type InviteRole = 'member' | 'viewer'
export type InviteState = 'active' | 'exhausted' | 'expired' | 'revoked'

export interface InviteCodeItem {
  id: string
  code: string
  role: InviteRole
  max_uses: number
  used_count: number
  expires_at: string | null
  revoked_at: string | null
  created_at: string
  state: InviteState
}

export interface InviteCodeList {
  items: InviteCodeItem[]
  total: number
}

export interface CreateInviteCodePayload {
  role: InviteRole
  max_uses: number
  expires_in_days: number | null
}

export async function fetchInviteCodes(): Promise<InviteCodeList> {
  const { data } = await apiClient.get<InviteCodeList>('/admin/invite-codes')
  return data
}

export async function createInviteCode(payload: CreateInviteCodePayload): Promise<InviteCodeItem> {
  const { data } = await apiClient.post<InviteCodeItem>('/admin/invite-codes', payload)
  return data
}

export interface CreateInviteCodeBatchPayload extends CreateInviteCodePayload {
  count: number
}

/** 批量发码（1..20 张，同参数）。给新小组开几个人是常见动作。 */
export async function createInviteCodesBatch(
  payload: CreateInviteCodeBatchPayload
): Promise<InviteCodeList> {
  const { data } = await apiClient.post<InviteCodeList>('/admin/invite-codes/batch', payload)
  return data
}

export async function revokeInviteCode(codeId: string): Promise<InviteCodeItem> {
  const { data } = await apiClient.post<InviteCodeItem>(`/admin/invite-codes/${codeId}/revoke`)
  return data
}
