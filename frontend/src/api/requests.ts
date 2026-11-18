import { apiClient } from './client'

// ---------- 需求（T047/T048 + 007 分派）----------

export type RequestPriority = 'high' | 'normal' | 'low'
export type RequestStatus =
  'created' | 'pending' | 'accepted' | 'assigned' | 'running' | 'completed' | 'failed' | 'cancelled'

/** 007：经手链一条（详情页时间线，按 seq 正序）。 */
export interface DispatchRecord {
  id: string
  seq: number
  action: 'dispatch' | 'reassign' | 'accept' | 'reject'
  actor_id: string
  actor_email?: string | null
  from_assignee_id: string | null
  from_assignee_email?: string | null
  to_assignee_id: string | null
  to_assignee_email?: string | null
  reason: string | null
  created_at: string
}

export interface AttachmentMeta {
  key: string
  name: string
  size: number
  mime: string
}

export interface AttachmentUploadResult extends AttachmentMeta {
  /** 表单 POST 的目标地址（对象存储 bucket 根） */
  upload_url: string
  /** 需原样随表单提交的字段（含 policy 签名与 key） */
  upload_fields: Record<string, string>
}

/** 需求名下 Run 的结果构成（部分成功时用来解释「失败」到底失败了几项）。 */
export interface RunOutcomeSummary {
  total: number
  completed: number
  failed: number
  cancelled: number
  active: number
}

export interface WorkRequest {
  id: string
  owner_id: string
  title: string
  description: string | null
  expected_output: string | null
  priority: RequestPriority
  status: RequestStatus
  attachments: AttachmentMeta[]
  /** 尚未分配时为 null（后端不回 0/0 噪音） */
  runs_summary?: RunOutcomeSummary | null
  /**
   * 来源调度（004 US2）：手工建的需求为 null。
   * `source_schedule_name` 在规则被删除后同样为 null——
   * 需求本身保留，只是追溯链断了，界面据此退化显示。
   */
  source_schedule_id?: string | null
  source_schedule_name?: string | null
  /** 007：当前接收人（分派指针）。未分派为 null。 */
  assignee_id?: string | null
  assignee_email?: string | null
  dispatched_at?: string | null
  accepted_at?: string | null
  /** 007：经手链。仅详情端点填充，列表端为 null。 */
  dispatch_history?: DispatchRecord[] | null
  /** 008：业务归属。project_name 是快照（随项目改名同步）。 */
  project_id?: string | null
  project_name?: string | null
  created_at: string
  updated_at: string
}

export interface RequestCreateRequest {
  title: string
  description?: string | null
  expected_output?: string | null
  priority?: RequestPriority
  /** 008：业务归属（可空 = 不归类）。 */
  project_id?: string | null
}

export interface RequestList {
  items: WorkRequest[]
  total: number
}

/** 007：分派类动作的统一响应（accept/reject/dispatch-to-user）。 */
export interface DispatchActionResult {
  request_id: string
  status: RequestStatus
}

export interface AttachmentUploadParams {
  filename: string
  size: number
  mime_type: string
}

/**
 * 需求列表。
 * 007：scope=relevant（默认，我创建的 + 分派给我的）｜mine（仅我创建的）。
 * 默认值变更是后端契约变更——不传 scope 时看到的不只是自己的需求了。
 */
export async function fetchRequests(params?: {
  scope?: 'relevant' | 'mine'
  status?: RequestStatus
  q?: string
  priority?: RequestPriority
  /** 008：按项目过滤（uuid；不存在/他人 → 404） */
  project_id?: string
  /** 008：未归属需求。与 project_id 互斥（都传以 project_id 为准）。 */
  project?: 'none'
  page?: number
  page_size?: number
}): Promise<RequestList> {
  const { data } = await apiClient.get<RequestList>('/requests', { params })
  return data
}

/** 编辑需求（008 首次接线；含 project_id 时仅创建者可改）。 */
export async function updateRequest(
  id: string,
  payload: {
    title?: string
    description?: string | null
    expected_output?: string | null
    priority?: RequestPriority
    project_id?: string | null
  }
): Promise<WorkRequest> {
  const { data } = await apiClient.patch<WorkRequest>(`/requests/${id}`, payload)
  return data
}

/** 接收分派给我的需求（007：pending → accepted）。 */
export async function acceptRequest(requestId: string): Promise<DispatchActionResult> {
  const { data } = await apiClient.post<DispatchActionResult>(`/requests/${requestId}/accept`)
  return data
}

/** 退回分派来的需求（007：pending|accepted → created，原因必填）。 */
export async function rejectRequest(
  requestId: string,
  reason: string
): Promise<DispatchActionResult> {
  const { data } = await apiClient.post<DispatchActionResult>(`/requests/${requestId}/reject`, {
    reason,
  })
  return data
}

/** 需求详情。 */
export async function fetchRequest(id: string): Promise<WorkRequest> {
  const { data } = await apiClient.get<WorkRequest>(`/requests/${id}`)
  return data
}

/** 创建需求。 */
export async function createRequest(payload: RequestCreateRequest): Promise<WorkRequest> {
  const { data } = await apiClient.post<WorkRequest>('/requests', payload)
  return data
}

/** 附件上传：申请预签名 PUT URL。 */
export async function requestAttachmentUpload(
  requestId: string,
  params: AttachmentUploadParams
): Promise<AttachmentUploadResult> {
  const { data } = await apiClient.post<{ attachments: AttachmentUploadResult[] }>(
    `/requests/${requestId}/attachments`,
    null,
    { params }
  )
  return data.attachments[0]
}

/** 附件下载链接（预签名 GET URL，302 跟随后为真实下载）。 */
export function attachmentDownloadUrl(requestId: string, key: string): string {
  return `/api/v1/requests/${requestId}/attachments/${encodeURIComponent(key)}`
}
