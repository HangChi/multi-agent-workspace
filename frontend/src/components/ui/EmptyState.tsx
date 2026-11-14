/** 空状态：有图标、有解释、有下一步动作，而不是一句「暂无数据」。 */

import type { ReactNode } from 'react'

import { IconTile } from './Card'
import { Icon } from './Icons'
import type { IconName } from './Icons'
import { cn } from './cn'

interface EmptyStateProps {
  icon?: IconName
  title: string
  description?: ReactNode
  action?: ReactNode
  /** 虚线边框容器（页面级空态） */
  bordered?: boolean
  className?: string
}

export function EmptyState({
  icon = 'inbox',
  title,
  description,
  action,
  bordered = true,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-6 py-12 text-center',
        bordered && 'rounded-xl border border-dashed border-line-strong bg-surface/60',
        className,
      )}
    >
      <IconTile tone="slate" size="lg" className="mb-3">
        <Icon name={icon} className="h-5 w-5" />
      </IconTile>
      <p className="text-sm font-medium text-ink-soft">{title}</p>
      {description && (
        <p className="mt-1 max-w-sm text-xs leading-relaxed text-ink-muted">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
