/**
 * 规则表单（T051，US1）：模板字段 + 目标选择 + 周期表达式。
 *
 * 两个刻意的设计：
 *
 * 1. **cron 错误必须落到输入框上。** 后端把校验失败的字段放在
 *    `detail.field` 里（契约 §1 第 3/4 步的 422），这个信息只有挂到对应输入框
 *    才有价值。因此组件收 `errorField`，只有它等于 `cron_expr` 时才把
 *    `error` 交给 cron 的 `Input` —— 否则一条「目标不可用」的错误会把
 *    cron 输入框标红，用户改错了地方。
 * 2. **交集语义写在 cron 输入框正下方。** spec FR-004 要求"在用户填写处展示"。
 *    把它塞进页面底部的说明区块等于没展示——用户填完就走了。
 */

import { useEffect, useRef, useState } from 'react'

import { previewCron, Schedule } from '../../api/endpoints'
import type { RoutingStrategy, ScheduleTargetType, ScheduleTemplate } from '../../api/endpoints'
import { Button } from '../ui/Button'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Icon } from '../ui/Icons'
import { Input, Select, Textarea } from '../ui/Field'
import { OptionCards } from '../ui/Segmented'
import { cn } from '../ui/cn'

/** 目标选项的最小形状：Agent 与组都只需 id/name，外加停用态用于提示。 */
export interface TargetOption {
  id: string
  name: string
  status?: string
}

export interface ScheduleFormValues {
  name: string
  description: string | null
  cron_expr: string
  timezone: string
  template: ScheduleTemplate
  target: {
    type: ScheduleTargetType
    id: string
    strategy: RoutingStrategy | null
  }
  enabled: boolean
}

interface Props {
  /** 编辑时的初始值；新建时省略。 */
  initial?: Schedule | null
  agents: TargetOption[]
  groups: TargetOption[]
  onSubmit: (values: ScheduleFormValues) => void
  onCancel: () => void
  submitting?: boolean
  /** 服务端返回的可读错误。 */
  error?: string | null
  /** 该错误归属的字段名（后端 `detail.field`）。 */
  errorField?: string | null
  /**
   * 后端固定规则的错误（与表单内容无关）由页面用横幅展示；
   * 传 true 时本组件不再自己消费 `error`，避免同一句话出现两次。
   */
  suppressError?: boolean
}

/**
 * 时区候选。
 *
 * 默认为 Asia/Shanghai：本产品的使用者与服务器都在国内，
 * 让它成为默认值比让每个人每建一条规则都手选一次更省事。
 * 仍保留其他选项是因为跨时区协作时会真的用到。
 */
const TIMEZONES = [
  { value: 'Asia/Shanghai', label: 'Asia/Shanghai（北京时间 UTC+8）' },
  { value: 'Asia/Tokyo', label: 'Asia/Tokyo（东京 UTC+9）' },
  { value: 'Asia/Singapore', label: 'Asia/Singapore（新加坡 UTC+8）' },
  { value: 'Europe/London', label: 'Europe/London（伦敦）' },
  { value: 'America/New_York', label: 'America/New_York（纽约）' },
  { value: 'UTC', label: 'UTC（协调世界时）' },
]

const DEFAULT_TIMEZONE = 'Asia/Shanghai'

const PRIORITY_OPTIONS = [
  { value: 'high', label: '高', description: '优先排入执行队列' },
  { value: 'normal', label: '中', description: '常规优先级' },
  { value: 'low', label: '低', description: '空闲时执行' },
] as const

const STRATEGY_OPTIONS = [
  { value: 'fan_out', label: '并行拆分', description: '组内每个 Agent 各跑一份' },
  { value: 'pipeline', label: '串行接力', description: '按顺序逐个执行' },
  { value: 'router', label: '路由分发', description: '由组决定交给谁' },
] as const

/** 常用周期预设：让不会写 cron 的人也能建出一条能跑的规则。 */
const CRON_PRESETS = [
  { value: '0 9 * * 1-5', label: '工作日 09:00' },
  { value: '0 9 * * *', label: '每天 09:00' },
  { value: '0 */6 * * *', label: '每 6 小时' },
  { value: '30 18 * * 5', label: '每周五 18:30' },
  { value: '0 10 1 * *', label: '每月 1 号 10:00' },
]

/**
 * 交集语义提示。
 *
 * 措辞里必须同时出现「交集」和「两者都满足」，并且给一个具体例子：
 * 只写"取交集"仍有人不理解，只给例子又会被当成个例。这两半分开都守不住。
 */
const CRON_SEMANTICS_HINT =
  '「日期」与「星期」同时限定时按交集求值——两者都满足才触发。' +
  '例如 0 9 1 * 1 只在「既是 1 号又是周一」的那天 09:00 触发，不是「1 号或周一」。'

export function ScheduleForm({
  initial,
  agents,
  groups,
  onSubmit,
  onCancel,
  submitting,
  error,
  errorField,
  suppressError,
}: Props) {
  const [values, setValues] = useState<ScheduleFormValues>(() => ({
    name: initial?.name ?? '',
    description: initial?.description ?? '',
    cron_expr: initial?.cron_expr ?? '0 9 * * 1-5',
    timezone: initial?.timezone ?? DEFAULT_TIMEZONE,
    template: {
      title: initial?.template.title ?? '',
      description: initial?.template.description ?? '',
      expected_output: initial?.template.expected_output ?? '',
      priority: initial?.template.priority ?? 'normal',
    },
    target: {
      type: initial?.target.type ?? 'agent',
      id: initial?.target.id ?? '',
      strategy: initial?.target.strategy ?? null,
    },
    enabled: initial?.enabled ?? true,
  }))
  const [touched, setTouched] = useState(false)
  const [preview, setPreview] = useState<{ runs: string[]; error: string | null } | null>(null)
  /** 预览请求本身失败（网络/500）：与「表达式非法」分开——
   *  前者不阻塞提交，后者必须阻塞（否则必然吃后端 422） */
  const [previewFailed, setPreviewFailed] = useState(false)

  const set = <K extends keyof ScheduleFormValues>(key: K, value: ScheduleFormValues[K]) =>
    setValues((v) => ({ ...v, [key]: value }))

  const setTemplate = <K extends keyof ScheduleTemplate>(key: K, value: ScheduleTemplate[K]) =>
    setValues((v) => ({ ...v, template: { ...v.template, [key]: value } }))

  const setTarget = <K extends keyof ScheduleFormValues['target']>(
    key: K,
    value: ScheduleFormValues['target'][K]
  ) => setValues((v) => ({ ...v, target: { ...v.target, [key]: value } }))

  // 切换目标类型要清空 id：否则会带着 agent 的 uuid 去匹配组的选项，
  // 界面上表现为「明明选过目标了，却提示请选择」。
  const changeTargetType = (type: ScheduleTargetType) =>
    setValues((v) => ({
      ...v,
      target: { ...v.target, type, id: '', strategy: type === 'group' ? v.target.strategy : null },
    }))

  /**
   * 实时预览下次触发时间。
   *
   * 预览失败不打断输入：这是"边输边提示"的场景，输入到一半的表达式不是错误。
   * 350ms 防抖，避免每敲一个字符发一次请求。
   */
  const previewSeq = useRef(0)
  useEffect(() => {
    const expr = values.cron_expr.trim()
    if (!expr) {
      setPreview(null)
      setPreviewFailed(false)
      return
    }
    const seq = ++previewSeq.current
    const timer = window.setTimeout(() => {
      previewCron({ cron_expr: expr, timezone: values.timezone, count: 3 })
        .then((result) => {
          // 只认最后一次请求的结果：先发的慢响应不该覆盖后发的
          if (seq === previewSeq.current) {
            setPreview({ runs: result.next_runs, error: result.error })
            setPreviewFailed(false)
          }
        })
        .catch(() => {
          if (seq === previewSeq.current) {
            // 网络失败不等于表达式错误：不给字段标红，只提示预览不可用
            setPreview(null)
            setPreviewFailed(true)
          }
        })
    }, 350)
    return () => window.clearTimeout(timer)
  }, [values.cron_expr, values.timezone])

  const nameError = touched && !values.name.trim() ? '请填写规则名称' : null
  const titleError = touched && !values.template.title.trim() ? '请填写需求标题' : null
  const targetError = touched && !values.target.id ? '请选择执行目标' : null

  // cron 字段只认自己的错误，见文件头注释。
  // 预览判非法时同步钉到字段上并阻止提交——让用户看到黄色警告却仍然
  // 提交得动，只会被后端 422 打回。
  const cronPreviewError = preview?.error ?? null
  const cronServerError =
    errorField === 'cron_expr' ? (error ?? null) : cronPreviewError
  const targetServerError = errorField === 'target' ? (error ?? null) : null
  const bannerError = suppressError || errorField ? null : (error ?? null)

  const options = values.target.type === 'agent' ? agents : groups
  const activeOptions = options.filter((o) => o.status !== 'archived')

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    setTouched(true)
    // 预览判非法时直接拦在前端：后端只会回 422，体验上是「说了不听」
    if (cronPreviewError) return
    if (!values.name.trim() || !values.template.title.trim() || !values.target.id) return
    onSubmit({
      ...values,
      name: values.name.trim(),
      description: values.description?.trim() ? values.description.trim() : null,
      cron_expr: values.cron_expr.trim(),
      template: {
        title: values.template.title.trim(),
        description: values.template.description?.trim()
          ? values.template.description.trim()
          : null,
        expected_output: values.template.expected_output?.trim()
          ? values.template.expected_output.trim()
          : null,
        priority: values.template.priority,
      },
    })
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      {bannerError && <ErrorBanner error={bannerError} className="mt-0" />}

      {/* ---------------- 规则本身 ---------------- */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          label="规则名称"
          required
          value={values.name}
          onChange={(e) => set('name', e.target.value)}
          error={nameError}
          placeholder="每日晨报"
          maxLength={100}
        />
        <Input
          label="备注"
          value={values.description ?? ''}
          onChange={(e) => set('description', e.target.value)}
          placeholder="给团队汇总昨日进度（可留空）"
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
        <div>
          <Input
            label="周期表达式"
            required
            value={values.cron_expr}
            onChange={(e) => set('cron_expr', e.target.value)}
            error={
              cronServerError ?? (touched && !values.cron_expr.trim() ? '请填写周期表达式' : null)
            }
            hint={CRON_SEMANTICS_HINT}
            placeholder="0 9 * * 1-5"
            maxLength={120}
            className="font-mono"
            // 字段顺序图例：新手连「分 时 日 月 周」都不知道，
            // 交集提示管的是语义，图例管的是字段顺序
            labelExtra={<span className="font-mono">分 时 日 月 周</span>}
          />
          <div className="mt-2 flex flex-wrap gap-1" role="group" aria-label="常用周期预设">
            {CRON_PRESETS.map((preset) => (
              <button
                key={preset.value}
                type="button"
                aria-pressed={values.cron_expr === preset.value}
                onClick={() => set('cron_expr', preset.value)}
                className={cn(
                  'rounded-md border px-2 py-1 text-micro font-medium transition-colors',
                  values.cron_expr === preset.value
                    ? 'border-brand-200 bg-brand-50 text-accent'
                    : 'border-line bg-surface text-ink-muted hover:bg-surface-muted hover:text-ink-soft'
                )}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
        <Select
          label="时区"
          value={values.timezone}
          onChange={(e) => set('timezone', e.target.value)}
          wrapperClassName="sm:w-56"
        >
          {TIMEZONES.map((tz) => (
            <option key={tz.value} value={tz.value}>
              {tz.label}
            </option>
          ))}
        </Select>
      </div>

      {/* 下次触发时间：算不出时把原因贴在原处，而不是留一块空白 */}
      {preview && (
        <div
          className={cn(
            'rounded-lg border px-3 py-2 text-xs leading-relaxed',
            preview.error
              ? 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-300'
              : 'border-line-subtle bg-surface-muted text-ink-soft'
          )}
        >
          {preview.error ? (
            <span className="flex items-start gap-1.5">
              <Icon name="alert-triangle" className="mt-px h-3.5 w-3.5 shrink-0" />
              {preview.error}
            </span>
          ) : (
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-medium text-ink-muted">接下来触发：</span>
              {preview.runs.map((run) => (
                <span key={run} className="tabular-nums">
                  {new Date(run).toLocaleString('zh-CN')}
                </span>
              ))}
            </span>
          )}
        </div>
      )}

      {/* 预览请求失败（网络/500）：不阻塞提交，但要说清「预览不可用」 */}
      {previewFailed && !preview?.error && (
        <p className="flex items-center gap-1.5 text-micro text-ink-faint">
          <Icon name="info" className="h-3 w-3" />
          触发时间预览请求失败，不影响保存；可过一会再试。
        </p>
      )}

      {/* ---------------- 建单模板 ---------------- */}
      <div className="rounded-xl border border-line/80 bg-surface-muted/40 p-4">
        <p className="mb-3 flex items-center gap-1.5 text-ui font-semibold text-ink">
          <Icon name="file" className="h-3.5 w-3.5 text-ink-muted" />
          每次触发时创建的需求
        </p>
        <div className="space-y-4">
          <Input
            label="需求标题"
            required
            value={values.template.title}
            onChange={(e) => setTemplate('title', e.target.value)}
            error={titleError}
            placeholder="每日进度汇总"
            maxLength={200}
          />
          <Textarea
            label="需求描述"
            value={values.template.description ?? ''}
            onChange={(e) => setTemplate('description', e.target.value)}
            placeholder="汇总昨日所有完成与阻塞的需求"
          />
          <Textarea
            label="期望产出"
            value={values.template.expected_output ?? ''}
            onChange={(e) => setTemplate('expected_output', e.target.value)}
            placeholder="markdown 摘要"
          />
          <OptionCards
            name="优先级"
            value={values.template.priority}
            onChange={(v) => setTemplate('priority', v as ScheduleTemplate['priority'])}
            options={PRIORITY_OPTIONS.map((o) => ({ ...o }))}
            columns={3}
          />
        </div>
      </div>

      {/* ---------------- 执行目标 ---------------- */}
      <div className="space-y-3">
        <OptionCards
          name="执行目标类型"
          value={values.target.type}
          onChange={(v) => changeTargetType(v as ScheduleTargetType)}
          options={[
            { value: 'agent', label: '单个 Agent', description: '每次交给同一个 Agent 执行' },
            { value: 'group', label: 'Agent 组', description: '按组的策略拆分或接力' },
          ]}
          columns={2}
        />

        <Select
          label={values.target.type === 'agent' ? '选择 Agent' : '选择组'}
          required
          value={values.target.id}
          onChange={(e) => setTarget('id', e.target.value)}
          error={targetError ?? targetServerError}
          placeholder={activeOptions.length ? '请选择…' : '暂无可选目标'}
          hint={
            activeOptions.length === 0
              ? values.target.type === 'agent'
                ? '还没有活跃的 Agent，请先在 Agent 页创建'
                : '还没有 Agent 组，请先在 Agent 组页创建'
              : undefined
          }
        >
          {activeOptions.map((option) => (
            <option key={option.id} value={option.id}>
              {option.name}
            </option>
          ))}
        </Select>

        {/* 策略只对组有意义：选 agent 时藏起来，避免传一个会被后端 400 拒掉的组合 */}
        {values.target.type === 'group' && (
          <OptionCards
            name="组的执行策略"
            value={values.target.strategy ?? 'fan_out'}
            onChange={(v) => setTarget('strategy', v as RoutingStrategy)}
            options={STRATEGY_OPTIONS.map((o) => ({ ...o }))}
            columns={3}
          />
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-line-subtle pt-4">
        <Button type="button" variant="ghost" onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" loading={submitting} leftIcon="check">
          {initial ? '保存修改' : '创建规则'}
        </Button>
      </div>
    </form>
  )
}
