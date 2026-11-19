/**
 * 模板编辑表单（T030）。
 *
 * **刻意不新写一套配置表单**：模板的 10 个字段与 Agent 逐字同义，再写一份
 * Input/Select/Slider 就会出现两套会漂移的校验。共用 AgentConfigFields，
 * 本文件只表达模板特有的三处差异：
 * 1. 标题文案与撞名提示（模板名同一用户名下唯一）；
 * 2. 出网模式说明指向「具体 Agent 上配置域名白名单」；
 * 3. 工具区脚注：模板引用的工具被下线时，用模板创建会自动过滤并提示。
 *
 * 没有出站规则 / 外部工具授权区——那两项挂在**具体的 Agent**上，
 * 模板只是配置，实例化后才谈得上授权（spec Scope Boundaries）。
 */

import { useRef, useState } from 'react'

import { AgentConfigFields, ConfigSubmitBar } from './AgentConfigFields'
import { CONFIG_DEFAULTS, type AgentConfigValues } from './constants'
import { ErrorBanner } from '../ui/ErrorBanner'

/** 模板表单的值类型 = 共用配置值类型（向后兼容的别名）。 */
export type TemplateFormValues = AgentConfigValues

interface Props {
  initial?: Partial<TemplateFormValues> | null
  onSubmit: (values: TemplateFormValues) => void
  onCancel: () => void
  submitting?: boolean
  /** 提交失败内联展示（Modal 里页面级横幅会被遮罩挡住，用户看不见） */
  error?: string | null
  errorField?: 'name' | 'system_prompt' | null
}

export function TemplateForm({
  initial,
  onSubmit,
  onCancel,
  submitting,
  error,
  errorField,
}: Props) {
  const [values, setValues] = useState<TemplateFormValues>({
    ...CONFIG_DEFAULTS,
    ...initial,
  })
  const [touched, setTouched] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)

  const set = <K extends keyof TemplateFormValues>(key: K, value: TemplateFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }))
  }

  const submit = () => {
    setTouched(true)
    if (!values.name.trim() || !values.system_prompt.trim()) {
      const firstInvalid = !values.name.trim()
        ? document.getElementById('template-form-name')
        : document.getElementById('template-form-prompt')
      firstInvalid?.focus()
      firstInvalid?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      return
    }
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
    <form
      ref={formRef}
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
      className="space-y-5"
      noValidate
      onBlur={() => setTouched(true)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault()
          submit()
        }
      }}
    >
      <AgentConfigFields
        idPrefix="template-form"
        values={values}
        set={set}
        touched={touched}
        error={error}
        errorField={errorField}
        nameLabel="模板名称"
        nameHint="同一用户名下不能重名；撞名时系统会拒绝而不是自动加后缀"
        namePlaceholder="例如：代码审查助手"
        egressHint="限制模式需在具体 Agent 上配置域名白名单"
        toolsFootnote="模板引用的工具若在创建前被下线，用该模板创建时会自动过滤并提示，不会因此失败。"
      />

      <ConfigSubmitBar
        onCancel={onCancel}
        submitting={submitting}
        submitLabel="保存模板"
        submittingLabel="保存中…"
      />

      {error && errorField !== 'name' && errorField !== 'system_prompt' && (
        <ErrorBanner error={error} className="mt-0" />
      )}
    </form>
  )
}
