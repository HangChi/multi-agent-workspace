import { apiClient } from './client'

// ---------- Agent 模板与克隆（005）----------

export type TemplateVisibility = 'private' | 'team'

export interface AgentTemplate {
  id: string
  /** null = 内置模板（所有成员可见、只读）。 */
  owner_id: string | null
  /** 派生字段：owner_id 为空即为内置。后端不落这个布尔列。 */
  is_builtin: boolean
  /** 023：private / team；内置模板恒为 team */
  visibility: TemplateVisibility
  /** 026：共享者邮箱（模板市场里显示「谁共享的」） */
  owner_email?: string | null
  name: string
  description: string | null
  system_prompt: string
  model: string
  temperature: number
  max_steps: number
  max_run_seconds: number
  tools: string[]
  cost_limit_cents: number
  egress_mode: 'default' | 'restricted'
  created_at: string
  updated_at: string
}

export interface AgentTemplateList {
  items: AgentTemplate[]
  total: number
}

/** 与后端 AgentCreate 同构；tools 刻意宽松（失效项由服务端过滤并进 warning）。 */
export interface AgentFromTemplateRequest {
  name: string
  description?: string | null
  system_prompt: string
  model?: string
  temperature?: number
  max_steps?: number
  max_run_seconds?: number
  tools?: string[]
  cost_limit_cents?: number
  egress_mode?: 'default' | 'restricted'
}

export interface AgentSaveResponse {
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
  egress_mode: string
  status: 'active' | 'archived'
  source_template_id: string | null
  source_agent_id: string | null
  /** 保存成功但有事要知道：工具被过滤 / 限制模式无允许规则。 */
  warning: string | null
  created_at: string
  updated_at: string
}

/** 模板列表：内置（全部）+ 自己的。 */
export async function fetchAgentTemplates(params?: {
  scope?: 'all' | 'mine' | 'builtin'
  q?: string
  page?: number
  page_size?: number
}): Promise<AgentTemplateList> {
  const { data } = await apiClient.get<AgentTemplateList>('/agent-templates', { params })
  return data
}

/** 模板详情。 */
export async function fetchAgentTemplate(id: string): Promise<AgentTemplate> {
  const { data } = await apiClient.get<AgentTemplate>(`/agent-templates/${id}`)
  return data
}

/** 编辑自己的模板（内置模板 403）。 */
export async function updateAgentTemplate(
  id: string,
  payload: Partial<
    Omit<AgentTemplate, 'id' | 'owner_id' | 'is_builtin' | 'created_at' | 'updated_at'>
  >
): Promise<AgentTemplate> {
  const { data } = await apiClient.patch<AgentTemplate>(`/agent-templates/${id}`, payload)
  return data
}

/** 删除自己的模板（内置模板 403）。已由它创建的 Agent 不受影响。 */
export async function deleteAgentTemplate(id: string): Promise<void> {
  await apiClient.delete(`/agent-templates/${id}`)
}

/** 从模板创建 Agent（模板值打底 + 本 payload 覆盖）。 */
export async function createAgentFromTemplate(
  templateId: string,
  payload: AgentFromTemplateRequest
): Promise<AgentSaveResponse> {
  const { data } = await apiClient.post<AgentSaveResponse>(
    `/agents/from-template/${templateId}`,
    payload
  )
  return data
}

/** 一键克隆 Agent（无请求体）。 */
export async function cloneAgent(id: string): Promise<AgentSaveResponse> {
  const { data } = await apiClient.post<AgentSaveResponse>(`/agents/${id}/clone`)
  return data
}

/** 把自己的 Agent 另存为模板（配置快照）。 */
export async function saveAgentAsTemplate(
  id: string,
  payload: { name: string; description?: string | null }
): Promise<AgentTemplate> {
  const { data } = await apiClient.post<AgentTemplate>(`/agents/${id}/save-as-template`, payload)
  return data
}


// ---------- 版本历史 / 导入导出（023）----------

export interface TemplateVersion {
  id: string
  template_id: string
  version: number
  created_by: string | null
  created_at: string
}

export interface TemplateExportPayload {
  schema_version: number
  name: string
  description: string | null
  system_prompt: string
  model: string
  temperature: number
  max_steps: number
  max_run_seconds: number
  tools: string[]
  cost_limit_cents: number
  egress_mode: 'default' | 'restricted'
}

export async function fetchTemplateVersions(id: string): Promise<TemplateVersion[]> {
  const { data } = await apiClient.get<{ items: TemplateVersion[]; total: number }>(
    `/agent-templates/${id}/versions`
  )
  return data.items
}

/** 把某个历史版本写回模板（只还原配置，不还原名字与共享范围）。 */
export async function restoreTemplateVersion(id: string, version: number): Promise<AgentTemplate> {
  const { data } = await apiClient.post<AgentTemplate>(
    `/agent-templates/${id}/versions/${version}/restore`
  )
  return data
}

/** 导出为可移植 JSON（含 schema_version）。 */
export async function exportAgentTemplate(id: string): Promise<TemplateExportPayload> {
  const { data } = await apiClient.get<TemplateExportPayload>(`/agent-templates/${id}/export`)
  return data
}

/** 从导出的 JSON 建一份新模板（重名 409）。 */
export async function importAgentTemplate(
  payload: TemplateExportPayload
): Promise<AgentTemplate> {
  const { data } = await apiClient.post<AgentTemplate>('/agent-templates/import', payload)
  return data
}


/** 把可见的模板复制成自己的私有副本（026 模板市场）。 */
export async function duplicateAgentTemplate(id: string): Promise<AgentTemplate> {
  const { data } = await apiClient.post<AgentTemplate>(`/agent-templates/${id}/duplicate`)
  return data
}
