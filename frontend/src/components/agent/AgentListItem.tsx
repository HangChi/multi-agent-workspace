/** Agent 列表项（T037）：一屏看清模型 / 工具 / 成本上限 / 状态。 */

import { Agent } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { Dropdown, DropdownItem, DropdownSeparator } from '../ui/Dropdown'
import { Icon } from '../ui/Icons'
import type { IconName } from '../ui/Icons'
import { cn } from '../ui/cn'

interface Props {
  agent: Agent
  onEdit: () => void
  onArchive: () => void
  onDelete: () => void
  /** 一键克隆：拷配置，不带历史运行与授权（005 US2）。 */
  onClone: () => void
  /** 另存为模板：把当前配置快照成模板（005 US3）。 */
  onSaveAsTemplate: () => void
}

function Meta({ icon, children }: { icon: IconName; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-micro text-ink-muted">
      <Icon name={icon} className="h-3.5 w-3.5 text-ink-faint" />
      {children}
    </span>
  )
}

export function AgentListItem({
  agent,
  onEdit,
  onArchive,
  onDelete,
  onClone,
  onSaveAsTemplate,
}: Props) {
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
                  'truncate text-body font-semibold',
                  active ? 'text-ink' : 'text-ink-muted'
                )}
              >
                {agent.name}
              </h3>
              <Badge tone={active ? 'emerald' : 'slate'} dot pulse={active}>
                {active ? '活跃' : '已归档'}
              </Badge>
              {agent.source_agent_id && (
                <span className="inline-flex items-center gap-1 text-micro text-ink-faint">
                  <Icon name="copy" className="h-3 w-3" />
                  克隆而来
                </span>
              )}
              {agent.source_template_id && (
                <span className="inline-flex items-center gap-1 text-micro text-ink-faint">
                  <Icon name="layers" className="h-3 w-3" />
                  模板创建
                </span>
              )}
            </div>
            <p
              className={cn(
                'mt-1 line-clamp-2 text-ui leading-relaxed',
                agent.description ? 'text-ink-muted' : 'text-ink-faint'
              )}
            >
              {agent.description || '暂无描述'}
            </p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" variant="secondary" leftIcon="edit" onClick={onEdit}>
            编辑
          </Button>
          {/* 次要操作收进菜单：克隆/存模板/归档/删除是低频操作，
              平铺成四个按钮会让「编辑」这个主操作失去视觉优先度 */}
          <Dropdown
            ariaLabel={`更多操作：${agent.name}`}
            panelClassName="w-52"
            triggerClassName="h-9 w-9 justify-center border border-line-strong bg-surface text-ink-muted hover:border-ink-faint hover:text-ink"
            trigger={<Icon name="more-horizontal" className="h-4 w-4" />}
          >
            <DropdownItem
              icon="copy"
              onClick={onClone}
              description="复制配置成新 Agent，不带历史运行与授权"
            >
              克隆
            </DropdownItem>
            <DropdownItem
              icon="layers"
              onClick={onSaveAsTemplate}
              description="把当前配置保存为模板，之后可一键复用"
            >
              存为模板
            </DropdownItem>
            {active && (
              <DropdownItem icon="archive" onClick={onArchive}>
                归档
              </DropdownItem>
            )}
            <DropdownSeparator />
            <DropdownItem icon="trash" tone="danger" onClick={onDelete}>
              删除
            </DropdownItem>
          </Dropdown>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line-subtle bg-surface-muted/60 px-4 py-2.5">
        <span className="inline-flex items-center gap-1.5 rounded-md bg-surface px-1.5 py-0.5 font-mono text-micro text-ink-soft ring-1 ring-inset ring-line">
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
        <span className="ml-auto hidden text-micro text-ink-faint sm:block">
          温度 {agent.temperature.toFixed(1)} · 最多 {agent.max_steps} 步 · 超时{' '}
          {agent.max_run_seconds}s
        </span>
      </div>
    </Card>
  )
}
