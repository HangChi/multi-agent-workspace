/** 徽章 / 状态点：全站状态色的唯一出口，避免每处各写一套 bg-xxx-100。 */

import type { ReactNode } from 'react'

import { cn } from './cn'

export type BadgeTone = 'slate' | 'brand' | 'sky' | 'emerald' | 'amber' | 'rose' | 'orange'

/**
 * 状态色需要显式的深色变体：浅色主题用「50 底 + 700 字」，
 * 深色主题换成「500/10 底 + 300 字」，否则浅底会刺眼、深字会看不清。
 */
const TONES: Record<BadgeTone, string> = {
  slate: 'bg-surface-subtle text-ink-soft ring-line/70',
  brand: 'bg-brand-50 text-accent ring-brand-200/70',
  sky: 'bg-sky-50 text-sky-700 ring-sky-200/70 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-500/25',
  emerald:
    'bg-emerald-50 text-emerald-700 ring-emerald-200/70 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/25',
  amber:
    'bg-amber-50 text-amber-700 ring-amber-200/70 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/25',
  rose: 'bg-rose-50 text-rose-700 ring-rose-200/70 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-500/25',
  orange:
    'bg-orange-50 text-orange-700 ring-orange-200/70 dark:bg-orange-500/10 dark:text-orange-300 dark:ring-orange-500/25',
}

const DOTS: Record<BadgeTone, string> = {
  slate: 'bg-ink-faint',
  brand: 'bg-brand-500',
  sky: 'bg-sky-500',
  emerald: 'bg-emerald-500',
  amber: 'bg-amber-500',
  rose: 'bg-rose-500',
  orange: 'bg-orange-500',
}

interface BadgeProps {
  tone?: BadgeTone
  /** 前置状态圆点 */
  dot?: boolean
  /** 圆点呼吸动画（执行中状态用） */
  pulse?: boolean
  size?: 'sm' | 'md'
  className?: string
  children: ReactNode
}

export function Badge({
  tone = 'slate',
  dot,
  pulse,
  size = 'sm',
  className,
  children,
}: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full font-medium ring-1 ring-inset',
        size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-[13px]',
        TONES[tone],
        className
      )}
    >
      {dot && (
        <span className="relative flex h-1.5 w-1.5 shrink-0">
          {pulse && (
            <span
              className={cn('absolute inset-0 animate-ping rounded-full opacity-70', DOTS[tone])}
            />
          )}
          <span className={cn('relative h-1.5 w-1.5 rounded-full', DOTS[tone])} />
        </span>
      )}
      {children}
    </span>
  )
}
