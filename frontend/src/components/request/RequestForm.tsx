/** 创建需求表单（T048）：标题/描述/期望产出/优先级。 */

import { useState } from 'react'

import { RequestPriority } from '../../api/endpoints'

export interface RequestFormValues {
  title: string
  description: string
  expected_output: string
  priority: RequestPriority
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

export function RequestForm({ onSubmit, onCancel, submitting }: Props) {
  const [values, setValues] = useState<RequestFormValues>({
    title: '',
    description: '',
    expected_output: '',
    priority: 'normal',
  })

  const set = <K extends keyof RequestFormValues>(key: K, value: RequestFormValues[K]) => {
    setValues((v) => ({ ...v, [key]: value }))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!values.title.trim()) return
    onSubmit({
      ...values,
      description: values.description.trim() || '',
      expected_output: values.expected_output.trim() || '',
    })
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="mb-6 rounded-xl border border-slate-200 bg-white p-6"
    >
      <h2 className="mb-4 text-lg font-semibold text-slate-800">新建需求</h2>
      <div className="space-y-4">
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">
            标题 <span className="text-red-500">*</span>
          </label>
          <input
            value={values.title}
            onChange={(e) => set('title', e.target.value)}
            required
            placeholder="例如：生成季度销售报表"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">描述</label>
          <textarea
            value={values.description}
            onChange={(e) => set('description', e.target.value)}
            rows={3}
            placeholder="背景、上下文、约束条件..."
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-slate-700">
            期望产出
          </label>
          <input
            value={values.expected_output}
            onChange={(e) => set('expected_output', e.target.value)}
            placeholder="例如：一份 Markdown 报表 + 数据表格"
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="mb-2 block text-sm font-medium text-slate-700">优先级</label>
          <div className="flex gap-2">
            {PRIORITY_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => set('priority', opt.value)}
                className={`rounded-lg border px-4 py-2 text-sm font-medium transition-colors ${
                  values.priority === opt.value
                    ? 'border-blue-500 bg-blue-50 text-blue-700'
                    : 'border-slate-300 text-slate-500 hover:border-slate-400'
                }`}
              >
                {opt.label}
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
            disabled={submitting || !values.title.trim()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {submitting ? '提交中...' : '创建需求'}
          </button>
        </div>
      </div>
    </form>
  )
}
