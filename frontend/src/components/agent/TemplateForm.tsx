/**
 * 模板编辑表单（T030）。
 *
 * **刻意不新写一套配置表单**：模板的 10 个字段与 Agent 逐字同义，再写一份
 * Input/Select/Slider 就会出现两套会漂移的校验（后端用共享 schema + 反漂移
 * 单测解决同一问题，前端用「只包一层薄壳」解决）。
 *
 * 与 `AgentForm` 的差异只有三处，都在注释里说明为什么必须有：
 * 1. 工具清单来自 `GET /tools`（与 AgentForm 同一来源，不重复维护）；
 * 2. 没有出站规则 / 外部工具授权区——那两项挂在**具体的 Agent** 上，
 *    模板只是配置，实例化后才谈得上授权（spec Scope Boundaries）；
 * 3. 提交的是模板字段，不含 status。
 */

import { useEffect, useState } from 'react'

import { fetchTools } from '../../api/endpoints'
import type { ToolSpec } from '../../api/endpoints'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icons'
import { Input, NumberInput, Slider, Select, Textarea } from '../ui/Field'
import { cn } from '../ui/cn'

export interface TemplateFormValues {
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
  initial?: Partial<TemplateFormValues> | null
  onSubmit: (values: TemplateFormValues) => void
  onCancel: () => void
  submitting?: boolean
}

// 与后端 LLM_BASE_URL 指向的 OpenAI 兼容端点保持一致（与 AgentForm 同源）。
const MODELS = [
  'Deepseek-V4.1-Flash',
  'DeepSeek-V4-Pro-0813',
  'GLM-5.3',
  'Kimi-K3',
  'qwen-flash',
  'doubao-seed-2.0-pro',
]

const DEFAULTS: TemplateFormValues = {
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

export function TemplateForm({ initial, onSubmit, onCancel, submitting }: Props) {
  const [catalog, setCatalog] = useState<ToolSpec[]>([])
  const [toolsLoading, setToolsLoading] = useState(true)
  const [values, setValues] = useState<TemplateFormValues>({
    ...DEFAULTS,
    ...initial,
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

  const set = <K extends keyof TemplateFormValues>(key: K, value: TemplateFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }))
  }

  const toggleTool = (tool: string) => {
    set(
      'tools',
      values.tools.includes(tool) ? values.tools.filter((t) => t !== tool) : [...values.tools, tool]
    )
  }

  const nameError = touched && !values.name.trim() ? '请填写模板名称' : null
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
      <Input
        label="模板名称"
        required
        value={values.name}
        onChange={(e) => set('name', e.target.value)}
        onBlur={() => setTouched(true)}
        error={nameError}
        placeholder="例如：代码审查助手"
        maxLength={60}
        hint="同一用户名下不能重名；撞名时系统会拒绝而不是自动加后缀"
      />

      <Input
        label="描述"
        value={values.description ?? ''}
        onChange={(e) => set('description', e.target.value)}
        placeholder="一句话说明这个模板适合什么场景"
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

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="模型"
          value={values.model}
          onChange={(e) => set('model', e.target.value)}
          hint="模型决定能力上限与成本单价"
        >
          {(MODELS.includes(values.model) ? MODELS : [values.model, ...MODELS]).map((m) => (
            <option key={m} value={m}>
              {m}
              {MODELS.includes(m) ? '' : '（不在预设列表）'}
            </option>
          ))}
        </Select>
        <Select
          label="出网模式"
          value={values.egress_mode}
          onChange={(e) => set('egress_mode', e.target.value as 'default' | 'restricted')}
          hint="限制模式需在具体 Agent 上配置域名白名单"
        >
          <option value="default">默认放行（内网黑名单强制拦截）</option>
          <option value="restricted">限制模式（按 Agent 域名白名单放行）</option>
        </Select>
      </div>

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
        <p className="mt-2 text-xs text-ink-faint">
          模板引用的工具若在创建前被下线，用该模板创建时会自动过滤并提示，不会因此失败。
        </p>
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-line-subtle pt-4">
        <Button type="button" variant="secondary" onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" loading={submitting} leftIcon="check">
          {submitting ? '保存中…' : '保存模板'}
        </Button>
      </div>
    </form>
  )
}
