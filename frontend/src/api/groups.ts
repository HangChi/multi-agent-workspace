import { apiClient } from './client'

// ---------- Agent 组（T038）----------

export type RoutingStrategy = 'direct' | 'fan_out' | 'pipeline' | 'router'

export interface GroupMember {
  agent_id: string
  name: string
  position: number
}

export interface Group {
  id: string
  owner_id: string
  name: string
  description: string | null
  routing_strategy: RoutingStrategy
  status: 'active' | 'archived'
  created_at: string
  updated_at: string
  members: GroupMember[]
}

export interface GroupCreateRequest {
  name: string
  description?: string | null
  routing_strategy?: RoutingStrategy
}

export interface GroupList {
  items: Group[]
  total: number
}

export interface AddMemberRequest {
  agent_id: string
  position: number
}

/** 组列表。 */
export async function fetchGroups(params?: {
  page?: number
  page_size?: number
  /** 返回全部命中项（忽略分页）；看板当拖拽目标用，必须拿全集。 */
  all?: boolean
}): Promise<GroupList> {
  const { data } = await apiClient.get<GroupList>('/groups', { params })
  return data
}

/** 组详情。 */
export async function fetchGroup(id: string): Promise<Group> {
  const { data } = await apiClient.get<Group>(`/groups/${id}`)
  return data
}

/** 创建组。 */
export async function createGroup(payload: GroupCreateRequest): Promise<Group> {
  const { data } = await apiClient.post<Group>('/groups', payload)
  return data
}

/** 编辑组。 */
export async function updateGroup(
  id: string,
  payload: Partial<GroupCreateRequest>
): Promise<Group> {
  const { data } = await apiClient.patch<Group>(`/groups/${id}`, payload)
  return data
}

/** 添加组成员。 */
export async function addGroupMember(
  groupId: string,
  payload: AddMemberRequest
): Promise<GroupMember> {
  const { data } = await apiClient.post<GroupMember>(`/groups/${groupId}/members`, payload)
  return data
}

/** 移除组成员。 */
export async function removeGroupMember(groupId: string, agentId: string): Promise<void> {
  await apiClient.delete(`/groups/${groupId}/members/${agentId}`)
}

/** 保存成员排序。 */
export async function reorderGroupMembers(
  groupId: string,
  positions: { agent_id: string; position: number }[]
): Promise<Group> {
  const { data } = await apiClient.patch<Group>(`/groups/${groupId}/members`, { positions })
  return data
}

/** 删除组。 */
export async function deleteGroup(id: string): Promise<void> {
  await apiClient.delete(`/groups/${id}`)
}
