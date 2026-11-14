/**
 * 统一错误提示组件（T082）：全局一致的错误展示样式。
 *
 * 用法：
 *   <ErrorBanner error={error} onDismiss={() => setError(null)} />
 *   <ErrorBanner error={error} variant="inline" />
 *   <ErrorBanner error={error} retry={refetch} />
 *
 * 设计约定：
 * - 错误消息统一经 extractError() 提取（适配后端 `{"error": {code, message}}`）
 * - 默认 banner 变体：玫红底警示条 + 图标 + 可选重试/关闭
 * - inline 变体：紧凑红字，用于表单字段旁
 */

import { extractError } from '../../api/client'
import { Icon } from './Icons'
import { cn } from './cn'

interface ErrorBannerProps {
  error: unknown
  /** 显示变体：banner=警示条（默认），inline=紧凑红字 */
  variant?: 'banner' | 'inline'
  /** 关闭回调（提供后显示 × 按钮，并置为可关闭状态） */
  onDismiss?: () => void
  /** 重试回调（提供后显示「重试」按钮） */
  retry?: () => void
  className?: string
}

export function ErrorBanner({
  error,
  variant = 'banner',
  onDismiss,
  retry,
  className = '',
}: ErrorBannerProps) {
  if (!error) return null
  const message = extractError(error)

  if (variant === 'inline') {
    return (
      <p
        className={cn('flex items-start gap-1.5 text-sm leading-relaxed text-rose-600', className)}
        role="alert"
      >
        <Icon name="alert-circle" className="mt-0.5 h-4 w-4 shrink-0" />
        <span>{message}</span>
      </p>
    )
  }

  return (
    <div
      role="alert"
      className={cn(
        'mt-4 flex items-start gap-2.5 rounded-xl border border-rose-200 bg-rose-50/80 px-3.5 py-3 text-sm text-rose-700 dark:border-rose-500/25 dark:bg-rose-500/10 dark:text-rose-300',
        'animate-fade-in',
        className,
      )}
    >
      <Icon name="alert-circle" className="mt-0.5 h-4 w-4 shrink-0 text-rose-500 dark:text-rose-400" />
      <span className="min-w-0 flex-1 break-words leading-relaxed">{message}</span>
      <span className="flex shrink-0 items-center gap-1">
        {retry && (
          <button
            type="button"
            onClick={retry}
            className="inline-flex items-center gap-1 rounded-md border border-rose-300 bg-surface/70 px-2 py-1 text-xs font-medium text-rose-700 transition-colors hover:bg-surface dark:border-rose-500/30 dark:text-rose-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-400/50"
          >
            <Icon name="refresh" className="h-3 w-3" />
            重试
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            aria-label="关闭错误提示"
            onClick={onDismiss}
            className="rounded-md p-1 text-rose-400 transition-colors hover:bg-rose-100 hover:text-rose-600 dark:text-rose-300 dark:hover:bg-rose-500/15 dark:hover:text-rose-200"
          >
            <Icon name="x" className="h-3.5 w-3.5" />
          </button>
        )}
      </span>
    </div>
  )
}
