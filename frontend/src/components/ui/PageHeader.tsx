/**
 * 页头：紧凑式，对齐 Linear / Vercel / Height 这类工作台的做法。
 *
 * 为什么把大图标底板和 28px 标题去掉：
 * - 主图标 + 大标题 + 两行描述会占掉近 110px 的纵向空间，
 *   看板、列表这类「内容即界面」的页面会被推得很低；
 * - 主流工作台的页头是 20px 标题 + 一行说明 + 右侧操作，
 *   层级靠字重和灰度区分，不靠色块；
 * - 于是每屏能多出两三行真实内容，信息密度立刻不一样。
 *
 * 小屏仍然纵向堆叠，操作区换行到第二行且右对齐。
 */

import type { ReactNode } from 'react'

import { cn } from './cn'

interface PageHeaderProps {
  title: string
  description?: ReactNode
  /** 标题左侧的小图标（20px，与标题同排，不再做大底板） */
  icon?: ReactNode
  actions?: ReactNode
  /** 标题上方的小字（归属 / 分类） */
  eyebrow?: ReactNode
  className?: string
}

export function PageHeader({
  title,
  description,
  icon,
  actions,
  eyebrow,
  className,
}: PageHeaderProps) {
  return (
    <div
      className={cn(
        'flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between',
        className
      )}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        {icon && <span className="shrink-0 text-ink-faint [&>svg]:h-5 [&>svg]:w-5">{icon}</span>}
        <div className="min-w-0">
          {eyebrow && (
            <div className="mb-0.5 flex items-center gap-1.5 text-micro font-medium uppercase tracking-[0.1em] text-ink-faint">
              {eyebrow}
            </div>
          )}
          <h1 className="text-page-title-sm font-semibold text-ink sm:text-page-title">{title}</h1>
          {description && (
            <p className="mt-1 max-w-[72ch] text-ui leading-relaxed text-ink-muted">
              {description}
            </p>
          )}
        </div>
      </div>
      {actions && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">{actions}</div>
      )}
    </div>
  )
}
