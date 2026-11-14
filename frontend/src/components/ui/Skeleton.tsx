/** 骨架屏：列表/表格加载时的占位，避免「加载中…」造成布局跳动。 */

import { cn } from './cn'

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'relative overflow-hidden rounded-md bg-line-strong/50',
        className ?? 'h-4 w-full',
      )}
    >
      <span className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/70 to-transparent dark:via-white/10" />
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
