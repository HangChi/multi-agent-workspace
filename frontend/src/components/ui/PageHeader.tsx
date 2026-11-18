/**
 * 页头：标题 + 说明 + 右侧操作区，全站统一间距与层级。
 *
 * 版式取舍：
 * - 标题从 20/24px 抬到 24/28px 并收紧字距，让「我在哪一页」一眼可见；
 * - 说明文字限制在 ~68ch，长描述不会拉出一条难读的长行；
 * - 小标题（eyebrow）用于放归属信息，例如「设置 / 定时调度」，
 *   这样面包屑只出现在页头一处，顶栏可以更干净。
 */

import type { ReactNode } from 'react'

import { cn } from './cn'

interface PageHeaderProps {
  title: string
  description?: ReactNode
  /** 标题左侧图标底板 */
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
      className={cn('flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between', className)}
    >
      <div className="min-w-0">
        {eyebrow && (
          <div className="mb-1.5 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-[0.12em] text-ink-faint">
            {eyebrow}
          </div>
        )}
        <div className="flex min-w-0 items-start gap-3.5">
          {/* 图标与标题同排：有 eyebrow 时不会浮在小标题那一行上 */}
          {icon}
          <div className="min-w-0">
            <h1 className="text-page-title-sm font-semibold text-ink sm:text-page-title">
              {title}
            </h1>
            {description && (
              <p className="mt-1.5 max-w-[68ch] text-[13.5px] leading-relaxed text-ink-muted">
                {description}
              </p>
            )}
          </div>
        </div>
      </div>
      {actions && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">{actions}</div>
      )}
    </div>
  )
}
