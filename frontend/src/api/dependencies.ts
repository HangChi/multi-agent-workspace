import { apiClient } from './client'

// ---------- 需求依赖（009）----------

/** 依赖边。反向视图（dependents）复用同一形状，此时对端是后续方。 */
export interface DependencyEdge {
  id: string
  request_id: string
  dependency_id: string
  dependency_title: string | null
  dependency_status: string | null
  created_at: string
}

export interface DependencyEdgeList {
  items: DependencyEdge[]
  total: number
}

/** 详情端的依赖概要一条（含 terminal 判定，前端直接渲染）。 */
export interface DependencyBrief {
  id: string
  request_id: string
  title: string
  status: string
  terminal: boolean
}

/** 加边：request 依赖 dependency（后者是前置，先做）。 */
export async function addDependency(
  requestId: string,
  dependencyId: string
): Promise<DependencyEdge> {
  const { data } = await apiClient.post<DependencyEdge>(`/requests/${requestId}/dependencies`, {
    dependency_id: dependencyId,
  })
  return data
}

/** 删边（误连能改；被依赖方 owner-or-assignee 可操作）。 */
export async function removeDependency(requestId: string, dependencyId: string): Promise<void> {
  await apiClient.delete(`/requests/${requestId}/dependencies/${dependencyId}`)
}

/** 我依赖谁（前置列表，带标题与状态）。 */
export async function fetchDependencies(requestId: string): Promise<DependencyEdgeList> {
  const { data } = await apiClient.get<DependencyEdgeList>(`/requests/${requestId}/dependencies`)
  return data
}

/** 谁依赖我（反向视图）。 */
export async function fetchDependents(requestId: string): Promise<DependencyEdgeList> {
  const { data } = await apiClient.get<DependencyEdgeList>(`/requests/${requestId}/dependents`)
  return data
}
