/** 分配确认弹窗（T057）：拖到 Agent 直接确认 / 拖到组选策略（默认组配置）。 */

import { useState } from 'react'

import { Agent, Group, RoutingStrategy, WorkRequest } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { IconTile } from '../ui/Card'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Icon } from '../ui/Icons'
import type { IconName } from '../ui/Icons'
import { Modal } from '../ui/Modal'
import { OptionCards } from '../ui/Segmented'

const STRATEGY_OPTIONS: {
  value: RoutingStrategy
  label: string
  description: string
  icon: IconName
}[] = [
  {
    value: 'direct',
    label: '直接路由',
    description: '请求直接交给该 Agent 执行',
    icon: 'target',
  },
  {
    value: 'fan_out',
    label: '广播',
    description: '同时发给所有成员，汇总结果',
    icon: 'users',
  },
  {
    value: 'pipeline',
    label: '流水线',
    description: '按成员顺序串联，前一步产出作为下一步输入',
    icon: 'arrow-right',
  },
  {
    value: 'router',
    label: '路由器',
    description: '由模型按需求内容挑选最合适的成员',
    icon: 'sparkles',
  },
]

const STRATEGY_LABELS: Record<RoutingStrategy, string> = {
  direct: '直接路由',
  fan_out: '广播',
  pipeline: '流水线',
  router: '路由器',
}

const PRIORITY_LABEL: Record<string, string> = {
  high: '高',
  normal: '中',
  low: '低',
}

export type AssignTarget =
  | { kind: 'agent'; id: string }
  | { kind: 'group'; id: string }

interface Props {
  request: WorkRequest
  target: AssignTarget
  /** 拖到的目标详情（Agent 或组）。 */
  agent?: Agent
  group?: Group
  submitting: boolean
  error: string | null
  onConfirm: (payload: { strategy: RoutingStrategy }) => void
  onCancel: () => void
}

export function AssignModal({
  request,
  target,
  agent,
  group,
  submitting,
  error,
  onConfirm,
  onCancel,
}: Props) {
  // 拖到组时默认取组配置的路由策略
  const [strategy, setStrategy] = useState<RoutingStrategy>(group?.routing_strategy ?? 'direct')

  const isAgent = target.kind === 'agent'
  const targetName = isAgent ? (agent?.name ?? '该 Agent') : (group?.name ?? '该组')

  return (
    <Modal
      open
      onClose={onCancel}
      size="lg"
      title="确认分配"
      description={
        <>
          将需求「{request.title}」分配给{' '}
          <span className="font-medium text-ink-soft">{targetName}</span>
        </>
      }
      icon={
        <IconTile tone={isAgent ? 'brand' : 'sky'}>
          <Icon name={isAgent ? 'bot' : 'users'} className="h-[18px] w-[18px]" />
        </IconTile>
      }
      footer={
        <>
          <Button variant="secondary" onClick={onCancel}>
            取消
          </Button>
          <Button
            variant="primary"
            leftIcon="play"
            loading={submitting}
            onClick={() => onConfirm({ strategy })}
          >
            {submitting ? '分配中…' : '确认分配'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface-muted/80 px-3 py-2.5">
          <span className="text-xs text-ink-muted">优先级</span>
          <Badge tone={request.priority === 'high' ? 'rose' : request.priority === 'low' ? 'slate' : 'brand'}>
            {PRIORITY_LABEL[request.priority] ?? request.priority}
          </Badge>
          <span className="ml-1 text-xs text-ink-muted">执行方式</span>
          <Badge tone={isAgent ? 'emerald' : 'sky'} dot>
            {isAgent ? '直接分配' : STRATEGY_LABELS[strategy]}
          </Badge>
          {isAgent && agent && (
            <span className="ml-auto text-xs text-ink-faint">
              模型 {agent.model} · 上限 ¥{(agent.cost_limit_cents / 100).toFixed(2)}
            </span>
          )}
        </div>

        {!isAgent && group && (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-[13px] font-medium text-ink-soft">路由策略</p>
              <span className="text-xs text-ink-faint">{group.members.length} 个成员</span>
            </div>
            <OptionCards
              options={STRATEGY_OPTIONS}
              value={strategy}
              onChange={setStrategy}
              columns={2}
              name="路由策略"
            />
            {group.members.length === 0 && (
              <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-amber-50 px-2.5 py-2 text-xs text-amber-700 ring-1 ring-inset ring-amber-200/70 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/25">
                <Icon name="alert-triangle" className="mt-px h-3.5 w-3.5 shrink-0" />
                该组暂无成员，广播等策略将无法执行，请先到「Agent 组」页添加成员。
              </p>
            )}
          </div>
        )}

        {error && <ErrorBanner error={error} className="mt-0" />}
      </div>
    </Modal>
  )
}
