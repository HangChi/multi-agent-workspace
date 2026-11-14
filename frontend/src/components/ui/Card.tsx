/** 卡片容器：全站信息块的统一外壳（边框 + 轻投影 + 可选悬浮反馈）。 */

import type { ReactNode } from 'react'

import { cn } from './cn'

interface CardProps {
  className?: string
  children: ReactNode
  /** 悬浮抬升（可点击卡片用） */
  interactive?: boolean
  /** 内边距档位：none 用于表格/自带 padding 的场景 */
  padding?: 'none' | 'sm' | 'md' | 'lg'
  /**
   * 底色语义。放在这里而不是用 className 覆盖背景：
   * 同一个元素上出现两个 bg-* 类时，谁生效取决于 Tailwind 的生成顺序，
   * 曾导致「深色日志面板被 bg-surface 盖成白底」。
   */
  tone?: 'white' | 'slate' | 'amber' | 'dark' | 'none'
}

const PADDINGS = {
  none: '',
  sm: 'p-3.5',
  md: 'p-4 sm:p-5',
  lg: 'p-5 sm:p-6',
} as const

const TONES = {
  white: 'border-line/90 bg-surface',
  slate: 'border-line/90 bg-surface-muted',
  amber: 'border-amber-200 bg-amber-50/60 dark:border-amber-500/25 dark:bg-amber-500/10',
  dark: 'border-line-strong/80 bg-[#060b14]',
  none: 'border-transparent bg-transparent',
} as const

export function Card({
  className,
  children,
  interactive,
  padding = 'md',
  tone = 'white',
}: CardProps) {
  return (
    <div
      className={cn(
        'rounded-xl border shadow-card',
        TONES[tone],
        PADDINGS[padding],
        interactive &&
          'transition-[box-shadow,border-color,transform] duration-200 hover:-translate-y-0.5 hover:border-line-strong hover:shadow-pop',
        className,
      )}
    >
      {children}
    </div>
  )
}

interface CardHeaderProps {
  title: ReactNode
  description?: ReactNode
  icon?: ReactNode
  actions?: ReactNode
  className?: string
  /** 底部加分割线（列表类卡片） */
  divider?: boolean
}

export function CardHeader({
  title,
  description,
  icon,
  actions,
  className,
  divider,
}: CardHeaderProps) {
  return (
    <div
      className={cn(
        'flex items-start justify-between gap-3',
        divider && 'border-b border-line-subtle pb-3.5',
        className,
      )}
    >
      <div className="flex min-w-0 items-start gap-2.5">
        {icon}
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {description && (
            <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">{description}</p>
          )}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

/** 图标底板：卡片标题/空状态/统计卡统一的彩色小方块。 */
export function IconTile({
  children,
  tone = 'brand',
  size = 'md',
  className,
}: {
  children: ReactNode
  tone?: 'brand' | 'slate' | 'sky' | 'emerald' | 'amber' | 'rose'
  size?: 'sm' | 'md' | 'lg'
  className?: string
}) {
  const tones = {
    brand: 'bg-brand-50 text-accent ring-brand-100',
    slate: 'bg-surface-subtle text-ink-muted ring-line/70',
    sky: 'bg-sky-50 text-sky-600 ring-sky-100 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/25',
    emerald:
      'bg-emerald-50 text-emerald-600 ring-emerald-100 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/25',
    amber:
      'bg-amber-50 text-amber-600 ring-amber-100 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/25',
    rose: 'bg-rose-50 text-rose-600 ring-rose-100 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-500/25',
  } as const
  const sizes = {
    sm: 'h-7 w-7 rounded-lg',
    md: 'h-9 w-9 rounded-lg',
    lg: 'h-11 w-11 rounded-xl',
  } as const
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center justify-center ring-1 ring-inset',
        tones[tone],
        sizes[size],
        className,
      )}
    >
      {children}
    </span>
  )
}
