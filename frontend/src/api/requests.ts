import { apiClient } from './client'

// ---------- 需求（T047/T048）----------

export type RequestPriority = 'high' | 'normal' | 'low'
export type RequestStatus =
  'created' | 'assigned' | 'running' | 'completed' | 'failed' | 'cancelled'

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
  created_at: string
  updated_at: string
}

export interface RequestCreateRequest {
  title: string
  description?: string | null
  expected_output?: string | null
  priority?: RequestPriority
}

export interface RequestList {
  items: WorkRequest[]
  total: number
}

export interface AttachmentUploadParams {
  filename: string
  size: number
  mime_type: string
}

/** 需求列表（?status=&q=&priority=&page=&page_size=）。 */
export async function fetchRequests(params?: {
  status?: RequestStatus
  q?: string
  priority?: RequestPriority
  page?: number
  page_size?: number
}): Promise<RequestList> {
  const { data } = await apiClient.get<RequestList>('/requests', { params })
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
