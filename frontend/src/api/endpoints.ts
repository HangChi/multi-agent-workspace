/** API 端点封装（auth 部分，T025）。 */

import { apiClient } from './client'

export interface UserOut {
  id: string
  email: string
  role: 'admin' | 'member' | 'viewer'
  status: string
}

export interface TokenPair {
  access: string
  refresh: string
}

export interface RegisterRequest {
  email: string
  password: string
  invite_code: string
}

export interface LoginRequest {
  email: string
  password: string
}

export interface AuthResponse {
  user: UserOut
  tokens: TokenPair
}

/** 注册（邀请码）。 */
export async function register(payload: RegisterRequest): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>('/auth/register', payload)
  return data
}

/** 登录。 */
export async function login(payload: LoginRequest): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>('/auth/login', payload)
  return data
}

/** 刷新 token。 */
export async function refreshToken(refresh: string): Promise<TokenPair> {
  const { data } = await apiClient.post<{ tokens: TokenPair }>('/auth/refresh', {
    refresh_token: refresh,
  })
  return data.tokens
}

/** 当前用户。 */
export async function fetchMe(): Promise<UserOut> {
  const { data } = await apiClient.get<UserOut>('/auth/me')
  return data
}

// ---------- Agent（T037）----------

export interface Agent {
  id: string
  owner_id: string
  name: string
  description: string | null
  system_prompt: string
  model: string
  temperature: number
  max_steps: number
  max_run_seconds: number
  tools: string[]
  cost_limit_cents: number
  status: 'active' | 'archived'
  created_at: string
  updated_at: string
}

export interface AgentCreateRequest {
  name: string
  description?: string | null
  system_prompt: string
  model?: string
  temperature?: number
  max_steps?: number
  max_run_seconds?: number
  tools?: string[]
  cost_limit_cents?: number
}

export type AgentUpdateRequest = Partial<Omit<AgentCreateRequest, 'name'>> & {
  name?: string
}

export interface AgentList {
  items: Agent[]
  total: number
}

/** Agent 列表（?status=&q=）。 */
export async function fetchAgents(params?: {
  status?: 'active' | 'archived'
  q?: string
}): Promise<AgentList> {
  const { data } = await apiClient.get<AgentList>('/agents', { params })
  return data
}

/** 创建 Agent。 */
export async function createAgent(payload: AgentCreateRequest): Promise<Agent> {
  const { data } = await apiClient.post<Agent>('/agents', payload)
  return data
}

/** 编辑 Agent。 */
export async function updateAgent(
  id: string,
  payload: AgentUpdateRequest,
): Promise<Agent> {
  const { data } = await apiClient.patch<Agent>(`/agents/${id}`, payload)
  return data
}

/** 归档 Agent。 */
export async function archiveAgent(id: string): Promise<Agent> {
  const { data } = await apiClient.post<Agent>(`/agents/${id}/archive`)
  return data
}

/** 删除 Agent。 */
export async function deleteAgent(id: string): Promise<void> {
  await apiClient.delete(`/agents/${id}`)
}

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
export async function fetchGroups(): Promise<GroupList> {
  const { data } = await apiClient.get<GroupList>('/groups')
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
  payload: Partial<GroupCreateRequest>,
): Promise<Group> {
  const { data } = await apiClient.patch<Group>(`/groups/${id}`, payload)
  return data
}

/** 添加组成员。 */
export async function addGroupMember(
  groupId: string,
  payload: AddMemberRequest,
): Promise<GroupMember> {
  const { data } = await apiClient.post<GroupMember>(
    `/groups/${groupId}/members`,
    payload,
  )
  return data
}

/** 移除组成员。 */
export async function removeGroupMember(
  groupId: string,
  agentId: string,
): Promise<void> {
  await apiClient.delete(`/groups/${groupId}/members/${agentId}`)
}

/** 保存成员排序。 */
export async function reorderGroupMembers(
  groupId: string,
  positions: { agent_id: string; position: number }[],
): Promise<Group> {
  const { data } = await apiClient.patch<Group>(
    `/groups/${groupId}/members`,
    { positions },
  )
  return data
}

/** 删除组。 */
export async function deleteGroup(id: string): Promise<void> {
  await apiClient.delete(`/groups/${id}`)
}

// ---------- 需求（T047/T048）----------

export type RequestPriority = 'high' | 'normal' | 'low'
export type RequestStatus =
  | 'created'
  | 'assigned'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface AttachmentMeta {
  key: string
  name: string
  size: number
  mime: string
}

export interface AttachmentUploadResult extends AttachmentMeta {
  presigned_put_url: string
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
  params: AttachmentUploadParams,
): Promise<AttachmentUploadResult> {
  const { data } = await apiClient.post<{ attachments: AttachmentUploadResult[] }>(
    `/requests/${requestId}/attachments`,
    null,
    { params },
  )
  return data.attachments[0]
}

/** 附件下载链接（预签名 GET URL，302 跟随后为真实下载）。 */
export function attachmentDownloadUrl(requestId: string, key: string): string {
  return `/api/v1/requests/${requestId}/attachments/${encodeURIComponent(key)}`
}

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
  payload: AssignRequestPayload,
): Promise<AssignResult> {
  const { data } = await apiClient.post<AssignResult>(
    `/requests/${requestId}/assign`,
    payload,
  )
  return data
}

// ---------- 运行（T072，US5）----------

export type RunStatus =
  | 'queued'
  | 'running'
  | 'needs_input'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timeout'

export interface RunStep {
  id: string
  seq: number
  kind: 'llm' | 'tool' | 'final'
  status: 'running' | 'completed' | 'failed'
  tool_name: string | null
  duration_ms: number
  cost_cents: number
  created_at: string
}

export interface RunArtifact {
  id: string
  filename: string
  size_bytes: number
  mime_type: string | null
  created_at: string
}

export interface RunDetail {
  id: string
  request_id: string
  owner_id: string
  assignee_type: 'agent' | 'group'
  assignee_id: string
  strategy: 'direct' | 'fan_out' | 'pipeline' | 'router'
  status: RunStatus
  current_step: number
  result: Record<string, unknown> | null
  error: string | null
  cost_cents: number
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
  steps: RunStep[]
  artifacts: RunArtifact[]
}

/** 运行详情。 */
export async function fetchRun(id: string): Promise<RunDetail> {
  const { data } = await apiClient.get<RunDetail>(`/runs/${id}`)
  return data
}

/** 取消运行（200 cancelled）。 */
export async function cancelRun(id: string): Promise<{ status: string }> {
  const { data } = await apiClient.post<{ status: string }>(`/runs/${id}/cancel`)
  return data
}

/** 重试运行（202 queued，仅 failed/timeout）。 */
export async function retryRun(id: string): Promise<{ status: string }> {
  const { data } = await apiClient.post<{ status: string }>(`/runs/${id}/retry`)
  return data
}

/** 回复运行（200 running，仅 needs_input）。 */
export async function replyRun(id: string, message: string): Promise<{ status: string }> {
  const { data } = await apiClient.post<{ status: string }>(`/runs/${id}/reply`, {
    message,
  })
  return data
}

/** 制品下载 URL（302 预签名）。 */
export function artifactDownloadUrl(runId: string, artifactId: string): string {
  return `/api/v1/runs/${runId}/artifacts/${artifactId}`
}
