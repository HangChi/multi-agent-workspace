import { apiClient } from './client'

// ---------- 外部 MCP 工具市场（003 US2）----------

export interface MarketplaceListing {
  listing_id: string
  tool_version_id: string
  stable_name: string
  display_name: string
  description: string
  server_name: string
  version: string
  permissions: string[]
  restrictions: Record<string, unknown>
  status: 'published' | 'paused' | 'rejected' | 'revoked' | 'pending_review'
  validated_at: string | null
}

export interface MarketplaceList {
  items: MarketplaceListing[]
  total: number
}

export interface MarketplaceQuery {
  q?: string
  category?: string
  capability?: string
  maintainer?: string
  version?: string
  page?: number
  page_size?: number
}

export interface ReviewDecision {
  reason: string
}

export async function fetchMarketplaceTools(
  query: MarketplaceQuery = {}
): Promise<MarketplaceList> {
  const { data } = await apiClient.get<MarketplaceList>('/marketplace/tools', { params: query })
  return data
}

export async function fetchMarketplaceTool(listingId: string): Promise<MarketplaceListing> {
  const { data } = await apiClient.get<MarketplaceListing>(`/marketplace/tools/${listingId}`)
  return data
}

export async function approveExternalTool(
  serverId: string,
  versionId: string,
  payload: ReviewDecision
): Promise<MarketplaceListing> {
  const { data } = await apiClient.post<MarketplaceListing>(
    `/external-tool-servers/${serverId}/tools/${versionId}/approve`,
    payload
  )
  return data
}

export async function rejectExternalTool(
  serverId: string,
  versionId: string,
  payload: ReviewDecision
): Promise<MarketplaceListing> {
  const { data } = await apiClient.post<MarketplaceListing>(
    `/external-tool-servers/${serverId}/tools/${versionId}/reject`,
    payload
  )
  return data
}

export async function pauseMarketplaceTool(
  listingId: string,
  payload: ReviewDecision
): Promise<MarketplaceListing> {
  const { data } = await apiClient.post<MarketplaceListing>(
    `/marketplace/tools/${listingId}/pause`,
    payload
  )
  return data
}

export async function revokeMarketplaceTool(
  listingId: string,
  payload: ReviewDecision
): Promise<MarketplaceListing> {
  const { data } = await apiClient.post<MarketplaceListing>(
    `/marketplace/tools/${listingId}/revoke`,
    payload
  )
  return data
}

export async function resumeMarketplaceTool(
  listingId: string,
  payload: ReviewDecision
): Promise<MarketplaceListing> {
  const { data } = await apiClient.post<MarketplaceListing>(
    `/marketplace/tools/${listingId}/resume`,
    payload
  )
  return data
}

export async function upgradeMarketplaceInstallation(
  installationId: string,
  payload: { listing_id: string; reason: string }
): Promise<ToolInstallation> {
  const { data } = await apiClient.post<ToolInstallation>(
    `/marketplace/installations/${installationId}/upgrade`,
    payload
  )
  return data
}

export async function rotateExternalCredential(
  serverId: string,
  payload: { label: string; kind?: 'bearer' | 'api_key'; value: string }
): Promise<{ id: string; label: string; kind: string; status: string; key_version: string }> {
  const { data } = await apiClient.post(
    `/external-tool-servers/${serverId}/credentials/rotate`,
    payload
  )
  return data
}

export async function revokeExternalCredential(
  serverId: string,
  credentialId: string
): Promise<void> {
  await apiClient.post(`/external-tool-servers/${serverId}/credentials/${credentialId}/revoke`)
}

export interface ExternalInvocationAudit {
  id: string
  run_id: string
  owner_id: string
  agent_id: string | null
  server_id: string
  tool_version_id: string
  call_id: string
  status: string
  duration_ms: number
  policy_result: Record<string, unknown>
  error_summary: string | null
  created_at: string
  completed_at: string | null
}

export async function fetchExternalInvocationAudit(
  query: {
    server_id?: string
    run_id?: string
    status?: string
    page?: number
    page_size?: number
  } = {}
): Promise<{ items: ExternalInvocationAudit[]; total: number }> {
  const { data } = await apiClient.get<{ items: ExternalInvocationAudit[]; total: number }>(
    '/audit-logs/external-invocations',
    { params: query }
  )
  return data
}

export interface ExternalToolVersion {
  id: string
  external_name: string
  stable_name: string
  version: string
  description: string
  required_permissions: string[]
  availability: string
  listing_id: string | null
  listing_status: string | null
}

/** 运行期诊断计数器（进程本地，admin only）。
 *
 * 注意 `counters` 是**单个 API 进程内**的累加值：多 worker 部署时每个进程各有一份，
 * 页面展示的是「当前这个进程」的视图，不是全局聚合。需要全局口径时看 Prometheus。 */
export interface ExternalObservabilityAlert {
  name: string
  count: number
  last_seen: string
}

export interface ExternalObservability {
  counters: Record<string, number>
  last_seen: Record<string, string>
  alerts: ExternalObservabilityAlert[]
}

export async function fetchExternalObservability(minutes = 60): Promise<ExternalObservability> {
  const { data } = await apiClient.get<ExternalObservability>('/external-tools/observability', {
    params: { minutes },
  })
  return data
}

export interface ExternalServer {
  /** 024：http（Streamable HTTP）/ stdio（本地子进程） */
  transport?: 'http' | 'stdio'
  command?: string | null
  args?: string[]
  env_keys?: string[]
  id: string
  name: string
  endpoint_url: string
  maintainer: string
  status: string
  current_validation_status: string | null
  credentials?: { id: string; label: string; kind: string; status: string; key_version: string }[]
  tools: ExternalToolVersion[]
}

export async function fetchExternalServers(): Promise<{ items: ExternalServer[]; total: number }> {
  const { data } = await apiClient.get<{ items: ExternalServer[]; total: number }>(
    '/external-tool-servers'
  )
  return data
}

export interface ToolInstallation {
  id: string
  listing_id: string
  owner_id: string
  status: string
  version_policy: 'pinned' | 'allow_patch'
  installed_at: string
}

export interface ExternalGrant {
  id: string
  agent_id: string
  installation_id: string
  tool_version_id: string
  stable_name: string
  version: string
  permissions: string[]
  status: 'active' | 'disabled' | 'revoked'
  unavailable_reason: string | null
  credential: { id: string; label: string; kind: string; status: string } | null
}

export async function installMarketplaceTool(
  listingId: string,
  version_policy: 'pinned' | 'allow_patch' = 'pinned'
): Promise<ToolInstallation> {
  const { data } = await apiClient.post<ToolInstallation>(
    `/marketplace/tools/${listingId}/install`,
    { version_policy }
  )
  return data
}

export async function fetchMarketplaceInstallations(): Promise<{
  items: ToolInstallation[]
  total: number
}> {
  const { data } = await apiClient.get<{ items: ToolInstallation[]; total: number }>(
    '/marketplace/installations'
  )
  return data
}

export async function fetchExternalToolGrants(
  agentId: string
): Promise<{ items: ExternalGrant[]; total: number }> {
  const { data } = await apiClient.get<{ items: ExternalGrant[]; total: number }>(
    `/agents/${agentId}/external-tool-grants`
  )
  return data
}

export async function createExternalToolGrant(
  agentId: string,
  payload: {
    installation_id: string
    tool_version_id: string
    permissions: string[]
    credential_id?: string
  }
): Promise<ExternalGrant> {
  const { data } = await apiClient.post<ExternalGrant>(
    `/agents/${agentId}/external-tool-grants`,
    payload
  )
  return data
}

export async function revokeExternalToolGrant(agentId: string, grantId: string): Promise<void> {
  await apiClient.delete(`/agents/${agentId}/external-tool-grants/${grantId}`)
}
