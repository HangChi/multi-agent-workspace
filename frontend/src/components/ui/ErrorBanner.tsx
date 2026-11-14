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
 * - 默认 banner 变体：红底警示条 + 可关闭
 * - inline 变体：紧凑红字，用于表单字段旁
 */

import { extractError } from '../../api/client'

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
      <p className={`text-sm text-red-600 ${className}`} role="alert">
        {message}
      </p>
    )
  }

  return (
    <div
      role="alert"
      className={`mt-4 flex items-start justify-between gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-600 ${className}`}
    >
      <span className="min-w-0 flex-1 break-words">{message}</span>
      <span className="flex shrink-0 items-center gap-2">
        {retry && (
          <button
            type="button"
            onClick={retry}
            className="rounded border border-red-300 px-2 py-0.5 text-xs text-red-600 hover:bg-red-100"
          >
            重试
          </button>
        )}
        {onDismiss && (
          <button
            type="button"
            aria-label="关闭错误提示"
            onClick={onDismiss}
            className="text-red-400 hover:text-red-600"
          >
            ✕
          </button>
        )}
      </span>
    </div>
  )
}
