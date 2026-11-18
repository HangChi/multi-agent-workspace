import { apiClient } from './client'

// ---------- 运行（T072，US5）----------

export type RunStatus =
  | 'queued'
  | 'running'
  | 'needs_input'
  /** 006：瞬时失败后等待自动重试（中间态，会自动回到 running 或转 failed） */
  | 'retrying'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timeout'

/** 006：一次失败的记录（retry_history 的元素）。 */
export interface RetryHistoryEntry {
  attempt: number
  error: string
  at: string
}

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
  sha256?: string | null
  created_at: string
}

export interface RunExecution {
  id: string
  step_seq: number
  language: string
  status: string
  exit_code: number | null
  duration_ms: number
  stdout_truncated: boolean
  artifacts: number
}

export interface ExecutionArtifactBrief {
  id: string
  filename: string
  size_bytes: number
}

export interface ExecutionDetail {
  id: string
  run_id: string
  step_seq: number
  language: string
  status: string
  error_category: string | null
  error_message: string | null
  exit_code: number | null
  code: string
  code_sha256: string
  stdout: string | null
  stderr: string | null
  stdout_truncated: boolean
  stdout_full_bytes: number
  duration_ms: number
  cpu_ms: number | null
  mem_peak_bytes: number | null
  image: string
  limits: Record<string, unknown>
  artifacts: ExecutionArtifactBrief[]
  artifact_truncated: { reason: string; message: string } | null
  created_at: string
  finished_at: string | null
}

export async function fetchExecution(runId: string, executionId: string): Promise<ExecutionDetail> {
  const { data } = await apiClient.get<ExecutionDetail>(`/runs/${runId}/executions/${executionId}`)
  return data
}

export interface EgressAccess {
  target_ip: string
  target_domain: string | null
  port: number
  action: 'allow' | 'deny'
  matched_rule_id?: string | null
  bytes_out?: number | null
  occurred_at: string
}

export interface EgressAccessList {
  items: EgressAccess[]
  total?: number
  summary?: Record<string, number>
}

export async function fetchExecutionEgress(
  runId: string,
  executionId: string
): Promise<EgressAccessList> {
  const { data } = await apiClient.get<EgressAccessList>(
    `/runs/${runId}/executions/${executionId}/egress`
  )
  return data
}

export interface EgressRule {
  id: string
  scope?: string
  agent_id?: string | null
  action?: string
  target_type: 'domain' | 'cidr' | 'ip'
  target: string
  port_range: string | null
  note: string | null
  enabled?: boolean
  builtin?: boolean
  created_by?: string | null
  created_at?: string | null
}

export interface EgressRuleList {
  items: EgressRule[]
  egress_mode?: 'default' | 'restricted'
}

export interface EgressRuleInput {
  target_type: 'domain' | 'cidr' | 'ip'
  target: string
  port_range?: string | null
  note?: string | null
}

export async function fetchAgentEgressRules(
  agentId: string,
  params?: {
    page?: number
    page_size?: number
    /** 返回全部规则（忽略分页）。编辑表单要能列出并逐条删除全部规则。 */
    all?: boolean
  }
): Promise<EgressRuleList> {
  const { data } = await apiClient.get<EgressRuleList>(`/agents/${agentId}/egress-rules`, {
    params,
  })
  return data
}

export async function createAgentEgressRule(
  agentId: string,
  payload: EgressRuleInput
): Promise<EgressRule> {
  const { data } = await apiClient.post<EgressRule>(`/agents/${agentId}/egress-rules`, payload)
  return data
}

export async function deleteAgentEgressRule(agentId: string, ruleId: string): Promise<void> {
  await apiClient.delete(`/agents/${agentId}/egress-rules/${ruleId}`)
}

export interface BlacklistRule {
  id: string
  target_type: 'domain' | 'cidr' | 'ip'
  target: string
  port_range: string | null
  note: string | null
  enabled: boolean
  builtin: boolean
  created_by?: string | null
  created_at?: string | null
}

export interface BlacklistList {
  items: BlacklistRule[]
}
export interface EgressAccessQuery {
  run_id?: string
  agent_id?: string
  action?: 'allow' | 'deny'
  page?: number
  page_size?: number
}

export async function fetchBlacklist(): Promise<BlacklistList> {
  const { data } = await apiClient.get<BlacklistList>('/admin/egress-blacklist')
  return data
}

export async function createBlacklist(payload: EgressRuleInput): Promise<BlacklistRule> {
  const { data } = await apiClient.post<BlacklistRule>('/admin/egress-blacklist', payload)
  return data
}

export async function deleteBlacklist(ruleId: string): Promise<void> {
  await apiClient.delete(`/admin/egress-blacklist/${ruleId}`)
}

export async function fetchEgressAccesses(
  query: EgressAccessQuery = {}
): Promise<EgressAccessList> {
  const { data } = await apiClient.get<EgressAccessList>('/admin/egress-accesses', {
    params: query,
  })
  return data
}

export interface RunExternalToolInvocation {
  id: string
  step_id: string | null
  server_name: string
  tool_name: string
  stable_name: string
  version: string
  status: string
  duration_ms: number
  error_category: string | null
  result_truncated: boolean
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
  /** 006：第几次尝试（首次为 1） */
  attempt: number
  /** 006：下次重试时刻；非重试中为 null */
  next_retry_at: string | null
  /** 006：历次失败原因 */
  retry_history: RetryHistoryEntry[]
  tool_grants_snapshot?: string[] | null
  result: Record<string, unknown> | null
  error: string | null
  cost_cents: number
  /** 精确成本（1 分 = 1e6 微分）；老数据可能没有 */
  cost_micros?: number
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
  steps: RunStep[]
  artifacts: RunArtifact[]
  executions?: RunExecution[]
  external_tool_invocations?: RunExternalToolInvocation[]
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

/**
 * 换取制品/附件的预签名下载地址。
 *
 * 直接用 `<a href>` 指向下载端点是拿不到 401 之后的 302 的：普通链接不会带
 * `Authorization` 头。这里用带鉴权的请求换 URL，再由浏览器跳转，对象存储始终
 * 只看到签名而不看到用户 token。
 */
export async function resolveArtifactDownloadUrl(
  runId: string,
  artifactId: string
): Promise<{ url: string; filename: string }> {
  const { data } = await apiClient.get<{ url: string; filename: string }>(
    `/runs/${runId}/artifacts/${artifactId}`,
    { params: { redirect: false } }
  )
  return data
}

export async function resolveAttachmentDownloadUrl(
  requestId: string,
  key: string
): Promise<{ url: string; filename: string }> {
  const { data } = await apiClient.get<{ url: string; filename: string }>(
    `/requests/${requestId}/attachments/${encodeURIComponent(key)}`,
    { params: { redirect: false } }
  )
  return data
}

/** 用隐藏链接打开预签名地址（不把 access token 交给对象存储）。 */
export function openDownloadUrl(url: string): void {
  const link = document.createElement('a')
  link.href = url
  link.rel = 'noreferrer'
  link.target = '_blank'
  document.body.appendChild(link)
  link.click()
  link.remove()
}
