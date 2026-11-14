/** Agent 相关常量（T037）。 */

/** 工具白名单（与后端 app/schemas/agent.py ALLOWED_TOOLS 保持一致）。 */
export const ALLOWED_TOOLS = [
  'web_search',
  'web_fetch',
  'shell',
  'http',
  'code_interpreter',
] as const
