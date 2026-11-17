import { apiClient } from './client'

// ---------- 工具目录（002 US2）----------

export interface ToolSpec {
  name: string
  display_name: string
  description: string
  kind: 'builtin' | 'sandbox'
  enabled: boolean
  param_schema: Record<string, unknown>
  timeout_seconds: number
}

export interface ToolList {
  items: ToolSpec[]
  total: number
}

export async function fetchTools(): Promise<ToolList> {
  const { data } = await apiClient.get<ToolList>('/tools')
  return data
}
