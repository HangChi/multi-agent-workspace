/**
 * Agent / 模板共用的常量与值类型（单一真实来源）。
 *
 * 此前 AgentForm 与 TemplateForm 各自复制了一份 MODELS 和默认值，
 * 改一处漏一处必然漂移；本文件是两边唯一的来源。
 */

// 与后端 LLM_BASE_URL 指向的 OpenAI 兼容端点保持一致：这几种都实测过
// 普通对话 + function calling。改这里要同步改 deploy/.env 的 LLM_MODEL 兜底值。
export const MODELS = [
  'Deepseek-V4.1-Flash',
  'DeepSeek-V4-Pro-0813',
  'GLM-5.3',
  'Kimi-K3',
  'qwen-flash',
  'doubao-seed-2.0-pro',
]

/** Agent 与模板的配置字段逐字同义（模板即配置快照），共用同一值类型。 */
export interface AgentConfigValues {
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

export const CONFIG_DEFAULTS: AgentConfigValues = {
  name: '',
  description: null,
  system_prompt: '',
  model: MODELS[0],
  temperature: 0.7,
  max_steps: 10,
  max_run_seconds: 300,
  tools: [],
  cost_limit_cents: 500,
  egress_mode: 'default',
}
