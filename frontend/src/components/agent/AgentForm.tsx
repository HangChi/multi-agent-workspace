/** Agent 创建/编辑表单（T037）。 */

import { useEffect, useState } from 'react'

import { Agent, fetchTools, ToolSpec } from '../../api/endpoints'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icons'
import { Input, NumberInput, Slider, Select, Textarea } from '../ui/Field'
import { cn } from '../ui/cn'

import { EgressRules } from './EgressRules'
import { ExternalToolGrants } from './ExternalToolGrants'

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
  egress_mode: 'default' | 'restricted'
}

interface Props {
  initial?: Agent | null
  onSubmit: (values: AgentFormValues) => void
  onCancel: () => void
  submitting?: boolean
}

// 与后端 LLM_BASE_URL 指向的 OpenAI 兼容端点保持一致：这几种都实测过
// 普通对话 + function calling。改这里要同步改 deploy/.env 的 LLM_MODEL 兜底值。
const MODELS = [
  'Deepseek-V4.1-Flash',
  'DeepSeek-V4-Pro-0813',
  'GLM-5.3',
  'Kimi-K3',
  'qwen-flash',
  'doubao-seed-2.0-pro',
]

export function AgentForm({ initial, onSubmit, onCancel, submitting }: Props) {
  const [catalog, setCatalog] = useState<ToolSpec[]>([])
  const [toolsLoading, setToolsLoading] = useState(true)
  const [values, setValues] = useState<AgentFormValues>({
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    system_prompt: initial?.system_prompt ?? '',
    model: initial?.model ?? MODELS[0],
    temperature: initial?.temperature ?? 0.7,
    max_steps: initial?.max_steps ?? 10,
    max_run_seconds: initial?.max_run_seconds ?? 300,
    tools: initial?.tools ?? [],
    cost_limit_cents: initial?.cost_limit_cents ?? 500,
    egress_mode: initial?.egress_mode ?? 'default',
  })
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    let active = true
    fetchTools()
      .then((result) => {
        if (active) setCatalog(result.items.filter((tool) => tool.enabled))
      })
      .catch(() => {
        if (active) setCatalog([])
      })
      .finally(() => {
        if (active) setToolsLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const set = <K extends keyof AgentFormValues>(key: K, value: AgentFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }))
  }

  const toggleTool = (tool: string) => {
    set(
      'tools',
      values.tools.includes(tool) ? values.tools.filter((t) => t !== tool) : [...values.tools, tool]
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
      egress_mode: values.egress_mode,
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
          {/*
            存量 Agent 的 model 可能是切换端点前的名字（如 gpt-4o-mini），
            它不在 MODELS 里时受控 select 会显示成空白。把当前值补到第一项，
            让用户至少看到「它现在是什么」，再决定要不要改。
          */}
          {(MODELS.includes(values.model) ? MODELS : [values.model, ...MODELS]).map((m) => (
            <option key={m} value={m}>
              {m}
              {MODELS.includes(m) ? '' : '（不在预设列表）'}
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
        placeholder={
          '你是一个专业的代码审查助手。\n请从可读性、边界条件、安全隐患三个角度审查代码…'
        }
        hint="提示词里写清角色、输入输出格式与禁止事项，执行稳定性会明显提升"
        className="font-mono text-ui leading-relaxed"
      />

      <fieldset className="rounded-xl border border-line bg-surface-muted/60 p-4">
        <legend className="px-1.5 text-ui font-medium text-ink-soft">运行参数</legend>

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

      <Select
        label="出网模式"
        value={values.egress_mode}
        onChange={(e) => set('egress_mode', e.target.value as 'default' | 'restricted')}
        hint={
          values.egress_mode === 'default'
            ? '默认允许访问公网，同时拦截内网和云元数据地址。'
            : '仅允许下方已配置的 Agent 出站域名清单；内置黑名单始终生效。'
        }
      >
        <option value="default">默认放行（内网黑名单强制拦截）</option>
        <option value="restricted">限制模式（按 Agent 域名白名单放行）</option>
      </Select>

      {initial?.id && (
        <>
          <EgressRules agentId={initial.id} mode={values.egress_mode} />
          <ExternalToolGrants agentId={initial.id} />
        </>
      )}

      <div>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-ui font-medium text-ink-soft">工具白名单</span>
          <span className="text-xs text-ink-faint">
            {toolsLoading ? '加载中…' : `已选 ${values.tools.length} 个`}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {catalog.map((tool) => {
            const active = values.tools.includes(tool.name)
            return (
              <button
                key={tool.name}
                type="button"
                aria-pressed={active}
                onClick={() => toggleTool(tool.name)}
                title={tool.description}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-all duration-150',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 focus-visible:ring-offset-1',
                  active
                    ? 'border-brand-300 bg-brand-50 text-accent shadow-sm'
                    : 'border-line bg-surface text-ink-muted hover:border-line-strong hover:bg-surface-muted hover:text-ink-soft'
                )}
              >
                <Icon name={active ? 'check' : 'plus'} className="h-3 w-3" />
                <span className="font-mono">{tool.name}</span>
                <span
                  className={cn(
                    'font-sans text-micro',
                    active ? 'text-brand-400' : 'text-ink-faint'
                  )}
                >
                  {tool.display_name}
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
