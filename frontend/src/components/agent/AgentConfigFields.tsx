/**
 * Agent / 模板共用的配置字段区（T030 起）。
 *
 * 为什么抽这一层：AgentForm 与 TemplateForm 曾各自抄了 170 行逐字重复的
 * 配置表单（MODELS、工具目录 effect、运行参数 fieldset、工具白名单 chips），
 * 两套校验与默认值必然漂移。模板与 Agent 的配置字段本就逐字同义
 * （模板即配置快照），差异只在三处，全部通过 props 表达：
 * - nameLabel / nameHint：标题文案（Agent「名称」vs 模板「模板名称」+ 撞名提示）
 * - egressHint：出网模式说明（模板版要指向「具体 Agent 上配置」）
 * - toolsFootnote：工具区脚注（模板版说明下线自动过滤）
 *
 * 不含：出站规则 / 外部工具授权（挂在具体 Agent 上，模板实例化后才谈得上）、
 * 提交栏（两个表单的按钮文案与粘性行为不同）。
 */

import type { ReactNode } from 'react'

import { Button } from '../ui/Button'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Icon } from '../ui/Icons'
import { Input, NumberInput, Slider, Select, Textarea } from '../ui/Field'
import { Skeleton } from '../ui/Skeleton'
import { cn } from '../ui/cn'
import { MODELS, type AgentConfigValues } from './constants'
import { useToolsCatalog } from '../../hooks/useToolsCatalog'

interface Props {
  idPrefix: string
  values: AgentConfigValues
  set: <K extends keyof AgentConfigValues>(key: K, value: AgentConfigValues[K]) => void
  /** 是否已触发过校验（失焦/提交后）——控制错误文案的显示时机 */
  touched: boolean
  /** 服务端字段级错误（409 撞名等）钉到具体字段 */
  error?: string | null
  errorField?: 'name' | 'system_prompt' | null
  nameLabel: string
  nameHint?: string
  namePlaceholder: string
  egressHint?: string
  toolsFootnote?: ReactNode
}

export function AgentConfigFields({
  idPrefix,
  values,
  set,
  touched,
  error,
  errorField,
  nameLabel,
  nameHint,
  namePlaceholder,
  egressHint,
  toolsFootnote,
}: Props) {
  const { catalog, isLoading: toolsLoading, isError: toolsError, refetch: refetchTools } =
    useToolsCatalog()

  const toggleTool = (tool: string) => {
    set(
      'tools',
      values.tools.includes(tool) ? values.tools.filter((t) => t !== tool) : [...values.tools, tool]
    )
  }

  const nameError =
    (touched && !values.name.trim() ? `请填写${nameLabel}` : null) ??
    (errorField === 'name' ? error : null)
  const promptError =
    (touched && !values.system_prompt.trim() ? '请填写系统提示词' : null) ??
    (errorField === 'system_prompt' ? error : null)

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Input
          id={`${idPrefix}-name`}
          label={nameLabel}
          required
          value={values.name}
          onChange={(e) => set('name', e.target.value)}
          error={nameError}
          hint={nameHint}
          placeholder={namePlaceholder}
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
        id={`${idPrefix}-prompt`}
        label="系统提示词"
        required
        value={values.system_prompt}
        onChange={(e) => set('system_prompt', e.target.value)}
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
          egressHint ??
          (values.egress_mode === 'default'
            ? '默认允许访问公网，同时拦截内网和云元数据地址。'
            : '仅允许下方已配置的 Agent 出站域名清单；内置黑名单始终生效。')
        }
      >
        <option value="default">默认放行（内网黑名单强制拦截）</option>
        <option value="restricted">限制模式（按 Agent 域名白名单放行）</option>
      </Select>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-ui font-medium text-ink-soft">工具白名单</span>
          <span className="text-xs text-ink-faint">
            {toolsLoading ? '加载中…' : `已选 ${values.tools.length} 个`}
          </span>
        </div>
        {toolsError ? (
          <ErrorBanner error="工具目录加载失败，暂时无法选择工具" retry={() => void refetchTools()} />
        ) : (
          <div role="group" aria-label="工具白名单" className="flex flex-wrap gap-2">
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
                    className={cn('font-sans text-micro', active ? 'text-brand-400' : 'text-ink-faint')}
                  >
                    {tool.display_name}
                  </span>
                </button>
              )
            })}
          </div>
        )}
        {toolsLoading && (
          <div className="flex flex-wrap gap-2" aria-hidden="true">
            <Skeleton className="h-8 w-28" />
            <Skeleton className="h-8 w-24" />
            <Skeleton className="h-8 w-32" />
          </div>
        )}
        {values.tools.length === 0 && !toolsError && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-300">
            <Icon name="alert-triangle" className="h-3.5 w-3.5" />
            未选择工具时，Agent 只能用模型自身知识作答
          </p>
        )}
        {toolsFootnote && <div className="mt-2 text-xs text-ink-faint">{toolsFootnote}</div>}
      </div>
    </div>
  )
}

/** 提交栏：sticky 变体给长表单（AgentForm），普通变体给模板弹窗。 */
export function ConfigSubmitBar({
  onCancel,
  submitting,
  submitLabel,
  submittingLabel,
  sticky,
  hint,
}: {
  onCancel: () => void
  submitting?: boolean
  submitLabel: string
  submittingLabel: string
  sticky?: boolean
  hint?: ReactNode
}) {
  return (
    <div
      className={cn(
        'flex items-center justify-between gap-2 border-t border-line-subtle pt-4',
        sticky &&
          'sticky bottom-0 -mx-5 -mb-5 rounded-b-xl border-t bg-surface/95 px-5 py-3 backdrop-blur'
      )}
    >
      {hint ? (
        <span className="hidden text-micro text-ink-faint sm:inline">{hint}</span>
      ) : (
        <span />
      )}
      <div className="flex items-center gap-2">
        <Button type="button" variant="secondary" onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" loading={submitting} leftIcon="check">
          {submitting ? submittingLabel : submitLabel}
        </Button>
      </div>
    </div>
  )
}
