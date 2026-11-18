import { apiClient } from './client'
import type { DispatchActionResult, RequestStatus } from './requests'
import type { RoutingStrategy } from './groups'

// ---------- 分配（T056-T058 + 007 分派）----------

export interface AssignRequestPayload {
  /**
   * 007：'user' = 拖到员工卡（仅转交处理责任，不触发执行）；
   * 'agent' / 'group' = 拖到执行目标（建 Run 入队）。
   */
  assignee_type: 'agent' | 'group' | 'user'
  assignee_id: string
  strategy?: RoutingStrategy
  /** 分派/转交留言（仅 user 落点有意义）。 */
  note?: string
}

export interface AssignResult {
  run_id: string
  status: 'queued'
}

/** 007：拖到员工的受理结果（无 run_id——不建 Run）。 */
export type DispatchResult = DispatchActionResult

/** 分配需求：拖员工返回 DispatchResult，拖 Agent/组返回 AssignResult。 */
export async function assignRequest(
  requestId: string,
  payload: AssignRequestPayload
): Promise<AssignResult | DispatchResult> {
  const { data } = await apiClient.post<AssignResult | DispatchResult>(
    `/requests/${requestId}/assign`,
    payload
  )
  return data
}

/** 判断一次 assign 的结果是「建了 Run」还是「仅转交」。 */
export function isDispatchResult(result: AssignResult | DispatchResult): result is DispatchResult {
  return 'request_id' in result && !('run_id' in result)
}

export type { RequestStatus }
