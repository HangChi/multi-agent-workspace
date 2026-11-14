/** Agent 创建/编辑表单（T037）。 */

import { useState } from 'react'

import { Agent } from '../../api/endpoints'

import { ALLOWED_TOOLS } from './constants'

export interface AgentFormValues {
  name: string
  description: string | null
  system_prompt: string
  model: string
  temperature: number
  max_steps: number
  max_run_seconds: number
  tools: string[]
  cost_limit_cents: number
}

interface Props {
  initial?: Agent | null
  onSubmit: (values: AgentFormValues) => void
  onCancel: () => void
  submitting?: boolean
}

const MODELS = [
  'gpt-4o',
  'gpt-4o-mini',
  'claude-3-5-sonnet',
  'claude-3-5-haiku',
  'deepseek-v3',
  'deepseek-r1',
]

export function AgentForm({ initial, onSubmit, onCancel, submitting }: Props) {
  const [values, setValues] = useState<AgentFormValues>({
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    system_prompt: initial?.system_prompt ?? '',
    model: initial?.model ?? MODELS[0],
    temperature: initial?.temperature ?? 0.7,
    max_steps: initial?.max_steps ?? 10,
    max_run_seconds: initial?.max_run_seconds ?? 300,
    tools: initial?.tools ?? ['web_search', 'web_fetch'],
    cost_limit_cents: initial?.cost_limit_cents ?? 500,
  })

  const set = <K extends keyof AgentFormValues>(key: K, value: AgentFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }))
  }

  const toggleTool = (tool: string) => {
    set(
      'tools',
      values.tools.includes(tool)
        ? values.tools.filter((t) => t !== tool)
        : [...values.tools, tool],
    )
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!values.name.trim()) return
    onSubmit({
      ...values,
      description: (values.description ?? '').trim() || null,
      temperature: Number(values.temperature),
      max_steps: Number(values.max_steps),
      max_run_seconds: Number(values.max_run_seconds),
      cost_limit_cents: Number(values.cost_limit_cents),
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">
            名称 <span className="text-red-500">*</span>
          </label>
          <input
            value={values.name}
            onChange={(e) => set('name', e.target.value)}
            required
            placeholder="例如：代码审查 Agent"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">模型</label>
          <select
            value={values.model}
            onChange={(e) => set('model', e.target.value)}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          >
            {MODELS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-slate-700">描述</label>
        <input
          value={values.description ?? ''}
          onChange={(e) => set('description', e.target.value)}
          placeholder="这个 Agent 负责什么？"
          className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        />
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-slate-700">
          系统提示词 <span className="text-red-500">*</span>
        </label>
        <textarea
          value={values.system_prompt}
          onChange={(e) => set('system_prompt', e.target.value)}
          required
          rows={5}
          placeholder="你是一个专业的代码审查助手……"
          className="w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-sm focus:border-blue-500 focus:outline-none"
        />
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">温度</label>
          <input
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={values.temperature}
            onChange={(e) => set('temperature', Number(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">最大步数</label>
          <input
            type="number"
            min={1}
            max={100}
            value={values.max_steps}
            onChange={(e) => set('max_steps', Number(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">
            超时（秒）
          </label>
          <input
            type="number"
            min={10}
            max={3600}
            value={values.max_run_seconds}
            onChange={(e) => set('max_run_seconds', Number(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">
            成本上限（分）
          </label>
          <input
            type="number"
            min={0}
            max={1000000}
            value={values.cost_limit_cents}
            onChange={(e) => set('cost_limit_cents', Number(e.target.value))}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
      </div>

      <div>
        <label className="mb-2 block text-sm font-medium text-slate-700">工具白名单</label>
        <div className="flex flex-wrap gap-2">
          {ALLOWED_TOOLS.map((tool) => (
            <button
              key={tool}
              type="button"
              onClick={() => toggleTool(tool)}
              className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                values.tools.includes(tool)
                  ? 'border-blue-500 bg-blue-50 text-blue-700'
                  : 'border-slate-300 text-slate-500 hover:border-slate-400'
              }`}
            >
              {tool}
            </button>
          ))}
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
        >
          取消
        </button>
        <button
          type="submit"
          disabled={submitting || !values.name.trim()}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {submitting ? '提交中...' : initial ? '保存修改' : '创建'}
        </button>
      </div>
    </form>
  )
}
