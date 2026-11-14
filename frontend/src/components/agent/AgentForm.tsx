/** Agent 创建/编辑表单（T037）。 */

import { useState } from 'react'

import { Agent } from '../../api/endpoints'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icons'
import { Input, NumberInput, Slider, Select, Textarea } from '../ui/Field'
import { cn } from '../ui/cn'

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

/** 工具的短说明，降低「猜字段」成本 */
const TOOL_HINTS: Record<string, string> = {
  web_search: '联网搜索',
  web_fetch: '抓取网页',
  shell: '执行命令',
  http: '调用接口',
  code_interpreter: '运行代码',
}

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
  const [touched, setTouched] = useState(false)

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

  const nameError = touched && !values.name.trim() ? '请填写 Agent 名称' : null
  const promptError = touched && !values.system_prompt.trim() ? '请填写系统提示词' : null

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (!values.name.trim() || !values.system_prompt.trim()) return
    onSubmit({
      ...values,
      name: values.name.trim(),
      description: (values.description ?? '').trim() || null,
      temperature: Number(values.temperature),
      max_steps: Number(values.max_steps),
      max_run_seconds: Number(values.max_run_seconds),
      cost_limit_cents: Number(values.cost_limit_cents),
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5" noValidate>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Input
          label="名称"
          required
          value={values.name}
          onChange={(e) => set('name', e.target.value)}
          onBlur={() => setTouched(true)}
          error={nameError}
          placeholder="例如：代码审查 Agent"
          maxLength={60}
        />
        <Select
          label="模型"
          value={values.model}
          onChange={(e) => set('model', e.target.value)}
          hint="模型决定能力上限与成本单价"
        >
          {MODELS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </Select>
      </div>

      <Input
        label="描述"
        value={values.description ?? ''}
        onChange={(e) => set('description', e.target.value)}
        placeholder="一句话说明这个 Agent 负责什么"
        maxLength={200}
      />

      <Textarea
        label="系统提示词"
        required
        value={values.system_prompt}
        onChange={(e) => set('system_prompt', e.target.value)}
        onBlur={() => setTouched(true)}
        error={promptError}
        rows={6}
        showCount
        maxLength={8000}
        placeholder={'你是一个专业的代码审查助手。\n请从可读性、边界条件、安全隐患三个角度审查代码…'}
        hint="提示词里写清角色、输入输出格式与禁止事项，执行稳定性会明显提升"
        className="font-mono text-[13px] leading-relaxed"
      />

      <fieldset className="rounded-xl border border-line bg-surface-muted/60 p-4">
        <legend className="px-1.5 text-[13px] font-medium text-ink-soft">运行参数</legend>

        <Slider
          label="温度"
          value={values.temperature}
          onValueChange={(v) => set('temperature', v)}
          min={0}
          max={2}
          step={0.1}
          displayValue={values.temperature.toFixed(1)}
          hint="越低越稳定保守，越高越发散；结构化任务建议 0.2~0.5"
          wrapperClassName="mb-4"
        />

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <NumberInput
            label="最大步数"
            value={values.max_steps}
            onValueChange={(v) => set('max_steps', v)}
            min={1}
            max={100}
            suffix="步"
            hint="单次运行最多调用轮数"
          />
          <NumberInput
            label="超时"
            value={values.max_run_seconds}
            onValueChange={(v) => set('max_run_seconds', v)}
            min={10}
            max={3600}
            step={10}
            suffix="秒"
            hint="超时后任务标记为 timeout"
          />
          <NumberInput
            label="成本上限"
            value={values.cost_limit_cents}
            onValueChange={(v) => set('cost_limit_cents', v)}
            min={0}
            max={1000000}
            step={100}
            suffix="分"
            hint={`约 ¥${(values.cost_limit_cents / 100).toFixed(2)}，超出即中止`}
          />
        </div>
      </fieldset>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[13px] font-medium text-ink-soft">工具白名单</span>
          <span className="text-xs text-ink-faint">已选 {values.tools.length} 个</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {ALLOWED_TOOLS.map((tool) => {
            const active = values.tools.includes(tool)
            return (
              <button
                key={tool}
                type="button"
                aria-pressed={active}
                onClick={() => toggleTool(tool)}
                title={TOOL_HINTS[tool]}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-all duration-150',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 focus-visible:ring-offset-1',
                  active
                    ? 'border-brand-300 bg-brand-50 text-accent shadow-sm'
                    : 'border-line bg-surface text-ink-muted hover:border-line-strong hover:bg-surface-muted hover:text-ink-soft',
                )}
              >
                <Icon name={active ? 'check' : 'plus'} className="h-3 w-3" />
                <span className="font-mono">{tool}</span>
                <span className={cn('font-sans text-2xs', active ? 'text-brand-400' : 'text-ink-faint')}>
                  {TOOL_HINTS[tool]}
                </span>
              </button>
            )
          })}
        </div>
        {values.tools.length === 0 && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-amber-600">
            <Icon name="alert-triangle" className="h-3.5 w-3.5" />
            未选择工具时，Agent 只能用模型自身知识作答
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-line-subtle pt-4">
        <Button type="button" variant="secondary" onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" loading={submitting} leftIcon="check">
          {submitting ? '保存中…' : initial ? '保存修改' : '创建 Agent'}
        </Button>
      </div>
    </form>
  )
}
