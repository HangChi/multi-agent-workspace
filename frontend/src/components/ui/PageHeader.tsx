/** 页头：标题 + 说明 + 右侧操作区，全站统一间距与层级。 */

import type { ReactNode } from 'react'

import { cn } from './cn'

interface PageHeaderProps {
  title: string
  description?: ReactNode
  /** 标题左侧图标底板 */
  icon?: ReactNode
  actions?: ReactNode
  className?: string
}

export function PageHeader({ title, description, icon, actions, className }: PageHeaderProps) {
  return (
    <div
      className={cn('flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between', className)}
    >
      <div className="flex min-w-0 items-start gap-3">
        {icon}
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-ink sm:text-2xl">{title}</h1>
          {description && (
            <p className="mt-1 text-sm leading-relaxed text-ink-muted">{description}</p>
          )}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}
