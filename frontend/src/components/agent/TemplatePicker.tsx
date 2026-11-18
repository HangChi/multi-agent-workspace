/**
 * 「从模板创建」选择弹层（T029）。
 *
 * 两个刻意的设计：
 *
 * 1. **内置模板只读且带角标**：它们是种子脚本注入的（005 US5），编辑/删除
 *    一律 403。这里连入口都不给，避免用户点了才被拒。
 * 2. **列表不分页**：模板量级与 Agent 同阶（数十到数百），一次取 `page_size=100`
 *    比做 Infinite Scroll 便宜；真到了几百条再改也不迟（与 SchedulesPage 的
 *    选择一致）。
 */

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchAgentTemplates } from '../../api/endpoints'
import type { AgentTemplate } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Input } from '../ui/Field'
import { Icon } from '../ui/Icons'
import { Modal } from '../ui/Modal'
import { CardSkeleton } from '../ui/Skeleton'
import { cn } from '../ui/cn'

interface Props {
  open: boolean
  onClose: () => void
  onPick: (template: AgentTemplate) => void
}

export function TemplatePicker({ open, onClose, onPick }: Props) {
  const [q, setQ] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: ['agent-templates', 'picker', q],
    queryFn: () => fetchAgentTemplates({ scope: 'all', q: q || undefined, page_size: 100 }),
    enabled: open,
  })

  const items = useMemo(() => data?.items ?? [], [data])
  const builtins = useMemo(() => items.filter((t) => t.is_builtin), [items])
  const mine = useMemo(() => items.filter((t) => !t.is_builtin), [items])

  const handleConfirm = () => {
    const picked = items.find((t) => t.id === selectedId)
    if (!picked) return
    onPick(picked)
    setSelectedId(null)
    setQ('')
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="从模板创建 Agent"
      description="选一个模板作为起点，表单会被预填，之后仍可改任何字段。"
      icon={
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-50 text-brand-600 ring-1 ring-inset ring-brand-100">
          <Icon name="layers" className="h-4.5 w-4.5" />
        </span>
      }
      size="xl"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" disabled={!selectedId} onClick={handleConfirm}>
            使用这个模板
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Input
          label="搜索模板"
          placeholder="按名称或描述搜索"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          leftIcon="search"
        />

        {error && <ErrorBanner error={error} variant="inline" />}

        {isLoading && (
          <div className="space-y-2">
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
          </div>
        )}

        {!isLoading && items.length === 0 && (
          <EmptyState
            icon="layers"
            title={q ? '没有匹配的模板' : '还没有可用模板'}
            description={
              q
                ? '换个关键词试试，或清空搜索查看全部模板。'
                : '内置模板会在部署时由种子脚本注入；你也可以把调好的 Agent 另存为模板。'
            }
          />
        )}

        {builtins.length > 0 && (
          <section>
            <h4 className="mb-2 text-ui font-medium text-ink-soft">内置模板（只读）</h4>
            <ul className="space-y-2">
              {builtins.map((t) => (
                <TemplateRow
                  key={t.id}
                  template={t}
                  selected={selectedId === t.id}
                  onSelect={() => setSelectedId(t.id)}
                />
              ))}
            </ul>
          </section>
        )}

        {mine.length > 0 && (
          <section>
            <h4 className="mb-2 text-ui font-medium text-ink-soft">我的模板</h4>
            <ul className="space-y-2">
              {mine.map((t) => (
                <TemplateRow
                  key={t.id}
                  template={t}
                  selected={selectedId === t.id}
                  onSelect={() => setSelectedId(t.id)}
                />
              ))}
            </ul>
          </section>
        )}
      </div>
    </Modal>
  )
}

function TemplateRow({
  template,
  selected,
  onSelect,
}: {
  template: AgentTemplate
  selected: boolean
  onSelect: () => void
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className={cn(
          'w-full rounded-xl border p-3 text-left transition-all duration-150',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40',
          selected
            ? 'border-brand-300 bg-brand-50/60 ring-1 ring-inset ring-brand-200'
            : 'border-line bg-surface hover:border-line-strong hover:bg-surface-muted'
        )}
      >
        <div className="flex items-center gap-2">
          <span className="truncate text-[14px] font-semibold text-ink">{template.name}</span>
          {template.is_builtin && (
            <Badge tone="slate" className="shrink-0">
              内置
            </Badge>
          )}
          <span className="ml-auto shrink-0 font-mono text-micro text-ink-faint">
            {template.model}
          </span>
        </div>
        <p className="mt-1 line-clamp-2 text-ui leading-relaxed text-ink-muted">
          {template.description || '暂无描述'}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-faint">
          <span className="inline-flex items-center gap-1">
            <Icon name="layers" className="h-3 w-3" />
            {template.tools.length > 0 ? template.tools.join(' · ') : '无工具'}
          </span>
          <span>温度 {template.temperature.toFixed(1)}</span>
          <span>最多 {template.max_steps} 步</span>
          <span>上限 ¥{(template.cost_limit_cents / 100).toFixed(2)}</span>
        </div>
      </button>
    </li>
  )
}
