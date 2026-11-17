import { apiClient } from './client'

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
  egress_mode: 'default' | 'restricted'
  status: 'active' | 'archived'
  /** 005：来源追溯。删模板/删源 Agent 后被后端置 null，不能据此判断来源是否还在。 */
  source_template_id?: string | null
  source_agent_id?: string | null
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
  egress_mode?: 'default' | 'restricted'
}

export type AgentUpdateRequest = Partial<Omit<AgentCreateRequest, 'name'>> & {
  name?: string
}

export interface AgentList {
  items: Agent[]
  total: number
}

/** Agent 列表（?status=&q= + 分页）。 */
export async function fetchAgents(params?: {
  status?: 'active' | 'archived'
  q?: string
  page?: number
  page_size?: number
  /**
   * 返回全部命中项（忽略分页）。
   * 看板/组页把结果当「拖拽目标 / 可选成员」，必须拿全集，
   * 否则超过一页的目标会静默消失。
   */
  all?: boolean
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
export async function updateAgent(id: string, payload: AgentUpdateRequest): Promise<Agent> {
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
