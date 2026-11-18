/**
 * 骨架屏：列表/表格加载时的占位，避免「加载中…」造成布局跳动。
 *
 * 骨架对读屏用户不可见（aria-hidden），但加载事实本身要可感知——
 * 容器统一补 role=status + sr-only 文案；单个骨架元件继续 aria-hidden，
 * 由最外层骨架容器承担播报，避免每块都念一遍「加载中」。
 */

import type { ReactNode } from 'react'

import { cn } from './cn'

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'relative overflow-hidden rounded-md bg-line-strong/50',
        className ?? 'h-4 w-full'
      )}
    >
      <span className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/70 to-transparent dark:via-white/10" />
    </div>
  )
}

/** 骨架容器：播报「加载中」。包住一组 Skeleton 使用。 */
export function SkeletonGroup({
  label = '加载中',
  className,
  children,
}: {
  label?: string
  className?: string
  children: ReactNode
}) {
  return (
    <div role="status" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  )
}

/** 卡片列表骨架：与真实卡片同高，切换时不闪。 */
export function CardSkeleton({ lines = 2 }: { lines?: number }) {
  return (
    <div className="rounded-xl border border-line/90 bg-surface p-4 shadow-card">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-2.5">
          <div className="flex items-center gap-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-4 w-14 rounded-full" />
          </div>
          {Array.from({ length: lines }).map((_, i) => (
            <Skeleton key={i} className={i === lines - 1 ? 'h-3 w-2/5' : 'h-3 w-4/5'} />
          ))}
        </div>
        <div className="flex gap-2">
          <Skeleton className="h-7 w-14 rounded-lg" />
          <Skeleton className="h-7 w-14 rounded-lg" />
        </div>
      </div>
    </div>
  )
}
