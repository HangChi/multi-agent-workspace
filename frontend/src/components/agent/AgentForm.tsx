/**
 * Agent 创建/编辑表单（T037）。
 *
 * 配置字段区与 TemplateForm 共用 AgentConfigFields（差异仅在文案与脚注，
 * 见那里的注释）；本文件只负责 Agent 特有的两块：
 * - 出站规则 / 外部工具授权（挂在具体 Agent 上，模板没有这两项）；
 * - 提交栏（sticky，长表单滚到底部不丢失保存入口）。
 */

import { useRef, useState } from 'react'

import { Agent } from '../../api/endpoints'
import { AgentConfigFields, ConfigSubmitBar } from './AgentConfigFields'
import { CONFIG_DEFAULTS, type AgentConfigValues } from './constants'
import { EgressRules } from './EgressRules'
import { ExternalToolGrants } from './ExternalToolGrants'
import { ErrorBanner } from '../ui/ErrorBanner'

/** Agent 表单的值类型 = 共用配置值类型（向后兼容的别名）。 */
export type AgentFormValues = AgentConfigValues

interface Props {
  initial?: Agent | null
  onSubmit: (values: AgentFormValues) => void
  onCancel: () => void
  submitting?: boolean
  /** 页面级错误（提交失败时由父级传入，内联展示在表单底部） */
  error?: string | null
  /** 服务端字段级错误定位（409 撞名等）：把错误钉到具体字段上 */
  errorField?: 'name' | 'system_prompt' | null
}

export function AgentForm({ initial, onSubmit, onCancel, submitting, error, errorField }: Props) {
  const [values, setValues] = useState<AgentConfigValues>({
    ...CONFIG_DEFAULTS,
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    system_prompt: initial?.system_prompt ?? '',
    model: initial?.model ?? CONFIG_DEFAULTS.model,
    temperature: initial?.temperature ?? 0.7,
    max_steps: initial?.max_steps ?? 10,
    max_run_seconds: initial?.max_run_seconds ?? 300,
    tools: initial?.tools ?? [],
    cost_limit_cents: initial?.cost_limit_cents ?? 500,
    egress_mode: initial?.egress_mode ?? 'default',
  })
  const [touched, setTouched] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)

  const set = <K extends keyof AgentConfigValues>(key: K, value: AgentConfigValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }))
  }

  const submit = () => {
    setTouched(true)
    if (!values.name.trim() || !values.system_prompt.trim()) {
      // 聚焦第一个无效字段并滚入视口：长表单里错误提示可能滚出视野
      const firstInvalid = !values.name.trim()
        ? document.getElementById('agent-form-name')
        : document.getElementById('agent-form-prompt')
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
      egress_mode: values.egress_mode,
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
      // ⌘/Ctrl+Enter 快捷提交：8000 字提示词不用拖回底部找按钮
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault()
          submit()
        }
      }}
    >
      <AgentConfigFields
        idPrefix="agent-form"
        values={values}
        set={set}
        touched={touched}
        error={error}
        errorField={errorField}
        nameLabel="名称"
        namePlaceholder="例如：代码审查 Agent"
      />

      {initial?.id && (
        <>
          <EgressRules agentId={initial.id} mode={values.egress_mode} />
          <ExternalToolGrants agentId={initial.id} />
        </>
      )}

      <ConfigSubmitBar
        onCancel={onCancel}
        submitting={submitting}
        submitLabel={initial ? '保存修改' : '创建 Agent'}
        submittingLabel="保存中…"
        sticky
        hint={
          <>
            <kbd className="rounded border border-line bg-surface-muted px-1">⌘</kbd> +{' '}
            <kbd className="rounded border border-line bg-surface-muted px-1">↵</kbd> 快速保存
          </>
        }
      />

      {/* 提交失败的内联错误（原来只在页面顶部横幅，离操作点很远） */}
      {error && errorField !== 'name' && errorField !== 'system_prompt' && (
        <ErrorBanner error={error} className="mt-0" />
      )}
    </form>
  )
}
