/** Agent 列表项（T037）：一屏看清模型 / 工具 / 成本上限 / 状态。 */

import { Agent } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { Icon } from '../ui/Icons'
import type { IconName } from '../ui/Icons'
import { cn } from '../ui/cn'

interface Props {
  agent: Agent
  onEdit: () => void
  onArchive: () => void
  onDelete: () => void
}

function Meta({ icon, children }: { icon: IconName; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-2xs text-ink-muted">
      <Icon name={icon} className="h-3.5 w-3.5 text-ink-faint" />
      {children}
    </span>
  )
}

export function AgentListItem({ agent, onEdit, onArchive, onDelete }: Props) {
  const active = agent.status === 'active'

  return (
    <Card padding="none" interactive className="group overflow-hidden">
      <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <span
            className={cn(
              'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-white shadow-sm ring-1 ring-inset',
              active
                ? 'bg-gradient-to-br from-sky-500 to-brand-600 ring-brand-700/20'
                : 'bg-line-strong ring-line-strong/40'
            )}
          >
            <Icon name="bot" className="h-[18px] w-[18px]" />
          </span>

          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3
                className={cn(
                  'truncate text-[15px] font-semibold',
                  active ? 'text-ink' : 'text-ink-muted'
                )}
              >
                {agent.name}
              </h3>
              <Badge tone={active ? 'emerald' : 'slate'} dot pulse={active}>
                {active ? '活跃' : '已归档'}
              </Badge>
            </div>
            <p
              className={cn(
                'mt-1 line-clamp-2 text-[13px] leading-relaxed',
                agent.description ? 'text-ink-muted' : 'text-ink-faint'
              )}
            >
              {agent.description || '暂无描述'}
            </p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <Button size="sm" variant="secondary" leftIcon="edit" onClick={onEdit}>
            编辑
          </Button>
          {active && (
            <Button size="sm" variant="ghost" leftIcon="archive" onClick={onArchive}>
              归档
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            leftIcon="trash"
            onClick={onDelete}
            className="text-rose-600 hover:bg-rose-50 hover:text-rose-700 dark:text-rose-300 dark:hover:bg-rose-500/15"
          >
            删除
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line-subtle bg-surface-muted/60 px-4 py-2.5">
        <span className="inline-flex items-center gap-1.5 rounded-md bg-surface px-1.5 py-0.5 font-mono text-2xs text-ink-soft ring-1 ring-inset ring-line">
          {agent.model}
        </span>
        <Meta icon="layers">{agent.tools.length > 0 ? agent.tools.join(' · ') : '无工具'}</Meta>
        <Meta icon="coins">上限 ¥{(agent.cost_limit_cents / 100).toFixed(2)}</Meta>
        <Meta icon="clock">
          {new Date(agent.created_at).toLocaleDateString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
          })}
        </Meta>
        <span className="ml-auto hidden text-2xs text-ink-faint sm:block">
          温度 {agent.temperature.toFixed(1)} · 最多 {agent.max_steps} 步 · 超时{' '}
          {agent.max_run_seconds}s
        </span>
      </div>
    </Card>
  )
}
