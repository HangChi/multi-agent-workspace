import { apiClient } from './client'
import type { RoutingStrategy } from './groups'

// ---------- 分配（T056-T058，US4）----------

export interface AssignRequestPayload {
  assignee_type: 'agent' | 'group'
  assignee_id: string
  strategy?: RoutingStrategy
}

export interface AssignResult {
  run_id: string
  status: 'queued'
}

/** 分配需求到 Agent/组（202 受理入队）。 */
export async function assignRequest(
  requestId: string,
  payload: AssignRequestPayload
): Promise<AssignResult> {
  const { data } = await apiClient.post<AssignResult>(`/requests/${requestId}/assign`, payload)
  return data
}
