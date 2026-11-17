/**
 * 轻提示（Toast）：操作成功/失败的即时反馈，替代「静默成功」和原生 alert。
 *
 * 用法：const toast = useToast(); toast.success('已创建') / toast.error(msg)
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { Icon } from './Icons'
import type { IconName } from './Icons'
import { cn } from './cn'
import { ToastContext } from './useToast'
import type { ToastApi, ToastTone } from './useToast'

interface ToastItem {
  id: number
  title: string
  description?: string
  tone: ToastTone
}

const TONE: Record<ToastTone, { icon: IconName; iconClass: string; bar: string }> = {
  success: {
    icon: 'check-circle',
    iconClass: 'text-emerald-600 dark:text-emerald-400',
    bar: 'bg-emerald-500',
  },
  error: {
    icon: 'alert-circle',
    iconClass: 'text-rose-600 dark:text-rose-400',
    bar: 'bg-rose-500',
  },
  info: { icon: 'info', iconClass: 'text-accent', bar: 'bg-brand-500' },
}

let seq = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const timers = useRef<number[]>([])

  const dismiss = useCallback((id: number) => {
    setItems((list) => list.filter((t) => t.id !== id))
  }, [])

  const push = useCallback(
    (tone: ToastTone, title: string, description?: string) => {
      const id = ++seq
      setItems((list) => [...list.slice(-3), { id, title, description, tone }])
      const timer = window.setTimeout(() => dismiss(id), 3800)
      timers.current.push(timer)
    },
    [dismiss]
  )

  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach((t) => window.clearTimeout(t))
  }, [])

  const api: ToastApi = {
    success: (title, description) => push('success', title, description),
    error: (title, description) => push('error', title, description),
    info: (title, description) => push('info', title, description),
  }

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed bottom-4 right-4 z-[90] flex w-[calc(100vw-2rem)] max-w-sm flex-col gap-2"
      >
        {items.map((item) => {
          const tone = TONE[item.tone]
          return (
            <div
              key={item.id}
              role="status"
              className="pointer-events-auto relative flex items-start gap-2.5 overflow-hidden rounded-xl border border-line/90 bg-surface p-3 pr-9 shadow-pop animate-slide-in-right"
            >
              <span className={cn('absolute inset-y-0 left-0 w-0.5', tone.bar)} />
              <Icon
                name={tone.icon}
                className={cn('mt-px h-[18px] w-[18px] shrink-0', tone.iconClass)}
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">{item.title}</p>
                {item.description && (
                  <p className="mt-0.5 break-words text-xs leading-relaxed text-ink-muted">
                    {item.description}
                  </p>
                )}
              </div>
              <button
                type="button"
                aria-label="关闭提示"
                onClick={() => dismiss(item.id)}
                className="absolute right-2 top-2 rounded-md p-1 text-ink-faint transition-colors hover:bg-surface-subtle hover:text-ink-soft"
              >
                <Icon name="x" className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}
