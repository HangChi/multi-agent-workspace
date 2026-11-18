/**
 * 创建需求表单（T048 + 008）：标题/描述/期望产出/优先级/项目归属。
 *
 * 项目选择器只列本人 **active** 项目（后端对归档项目返回 400——
 * 「看得见但不能用」），默认「不归类」：项目是可选的组织维度，
 * 不该逼每个人建需求前先建项目。
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchProjects, RequestPriority } from '../../api/endpoints'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icons'
import { Input, Select, Textarea } from '../ui/Field'
import { SegmentedControl } from '../ui/Segmented'
import { cn } from '../ui/cn'

export interface RequestFormValues {
  title: string
  description: string
  expected_output: string
  priority: RequestPriority
  /** 008：项目归属。null = 不归类。 */
  project_id: string | null
}

interface Props {
  onSubmit: (values: RequestFormValues) => void
  onCancel: () => void
  submitting?: boolean
}

const PRIORITY_OPTIONS: { value: RequestPriority; label: string }[] = [
  { value: 'high', label: '高' },
  { value: 'normal', label: '中' },
  { value: 'low', label: '低' },
]

const PRIORITY_HINT: Record<RequestPriority, string> = {
  high: '插队优先执行，适合线上问题与阻塞项',
  normal: '默认优先级，按队列顺序执行',
  low: '空闲时执行，适合调研与优化类需求',
}

export function RequestForm({ onSubmit, onCancel, submitting }: Props) {
  const [values, setValues] = useState<RequestFormValues>({
    title: '',
    description: '',
    expected_output: '',
    priority: 'normal',
    project_id: null,
  })
  const [touched, setTouched] = useState(false)

  // 项目下拉数据（本人 active 项目）。失败静默——选不了项目不影响建需求
  // 主流程， degrade 成「没有项目可选」而不是弹错误横幅。
  const { data: projects } = useQuery({
    // 010：relevant（含共享项目）——成员要能把自己的需求归到共享项目里
    // （s12 的 C3 现场：只查 mine 时表单没有共享项目）
    queryKey: ['projects', 'options', 'relevant'],
    queryFn: () => fetchProjects({ scope: 'relevant' }),
    retry: false,
  })
  const activeProjects = (projects?.items ?? []).filter((p) => p.status === 'active')

  const set = <K extends keyof RequestFormValues>(key: K, value: RequestFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }))
  }

  const titleError = touched && !values.title.trim() ? '请填写需求标题' : null

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (!values.title.trim()) return
    onSubmit({
      ...values,
      title: values.title.trim(),
      description: values.description.trim() || '',
      expected_output: values.expected_output.trim() || '',
    })
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="mb-4 rounded-xl border border-line/90 bg-surface shadow-card animate-fade-in-up"
    >
      <div className="flex items-start gap-3 border-b border-line-subtle px-5 py-4">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-accent ring-1 ring-inset ring-brand-100">
          <Icon name="plus" className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-ink">新建需求</h2>
          <p className="mt-0.5 text-xs text-ink-muted">
            描述清楚背景与期望产出，分配后 Agent 会按此执行。
          </p>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md p-1 text-ink-faint transition-colors hover:bg-surface-subtle hover:text-ink-soft"
          aria-label="收起表单"
        >
          <Icon name="chevron-up" className="h-4 w-4" />
        </button>
      </div>

      <div className="space-y-4 px-5 py-4">
        <Input
          label="标题"
          required
          value={values.title}
          onChange={(e) => set('title', e.target.value)}
          onBlur={() => setTouched(true)}
          error={titleError}
          placeholder="例如：生成 2026 Q1 销售分析报表"
          maxLength={120}
        />

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Textarea
            label="描述"
            value={values.description}
            onChange={(e) => set('description', e.target.value)}
            rows={3}
            showCount
            maxLength={2000}
            placeholder="背景、上下文、约束条件、可参考的资料…"
          />
          <Textarea
            label="期望产出"
            value={values.expected_output}
            onChange={(e) => set('expected_output', e.target.value)}
            rows={3}
            maxLength={500}
            placeholder="例如：一份 Markdown 报表 + 数据表格 CSV"
            hint="写清格式与结构，Agent 更容易一次做对"
          />
        </div>

        <Select
          label="所属项目（可选）"
          value={values.project_id ?? ''}
          onChange={(e) => set('project_id', e.target.value || null)}
          hint={
            activeProjects.length === 0
              ? '还没有项目——可到「项目管理」创建后回来归类'
              : '归类后可在看板按项目筛选；之后也可在详情里改'
          }
        >
          <option value="">不归类</option>
          {activeProjects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}（{p.request_count}）
            </option>
          ))}
        </Select>

        <div>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-ui font-medium text-ink-soft">优先级</span>
            <span className="text-xs text-ink-faint">{PRIORITY_HINT[values.priority]}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SegmentedControl
              options={PRIORITY_OPTIONS}
              value={values.priority}
              onChange={(v) => set('priority', v)}
              ariaLabel="优先级"
              className={cn(
                values.priority === 'high' && '[&_button[aria-pressed=true]]:text-rose-700'
              )}
            />
            <span className="text-xs text-ink-faint">
              提示：创建后可拖拽卡片到右侧 Agent / 组完成分配
            </span>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 rounded-b-xl border-t border-line-subtle bg-surface-muted/70 px-5 py-3">
        <Button type="button" variant="secondary" onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" loading={submitting} leftIcon="plus">
          {submitting ? '创建中…' : '创建需求'}
        </Button>
      </div>
    </form>
  )
}
