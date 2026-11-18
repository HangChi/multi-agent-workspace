/**
 * 轻提示（Toast）：操作成功/失败的即时反馈，替代「静默成功」和原生 alert。
 *
 * 用法：const toast = useToast(); toast.success('已创建') / toast.error(msg)
 *
 * 细节约定：
 * - error 停留 8s（成功 3.8s）：失败消息常带后端原因，用户要读完，
 *   且没有重试入口时更不能一闪而过；
 * - hover 暂停倒计时：正在读的提示不该被撤走；
 * - 容器只挂一处 aria-live：每条再挂 role=status 会被多数读屏重复播报；
 * - 计时器按 id 登记，dismiss 即注销，不在 timers 数组里无限堆积。
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

/** 停留时长：错误消息要留足阅读时间（含重试操作） */
const DURATION: Record<ToastTone, number> = {
  success: 3800,
  info: 4800,
  error: 8000,
}

let seq = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const timers = useRef(new Map<number, number>())

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id)
    if (timer !== undefined) {
      window.clearTimeout(timer)
      timers.current.delete(id)
    }
    setItems((list) => list.filter((t) => t.id !== id))
  }, [])

  const push = useCallback(
    (tone: ToastTone, title: string, description?: string) => {
      const id = ++seq
      setItems((list) => [...list.slice(-3), { id, title, description, tone }])
      timers.current.set(
        id,
        window.setTimeout(() => dismiss(id), DURATION[tone])
      )
    },
    [dismiss]
  )

  // 卸载时清掉所有在途计时器
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
        // hover 暂停：鼠标进入容器时清掉所有倒计时，离开后按剩余时间重启
        // （简化实现：整组重启计时——暂停语义正确，不会误撤正在读的提示）
        onMouseEnter={() => timers.current.forEach((t) => window.clearTimeout(t))}
        onMouseLeave={() =>
          items.forEach((item) => {
            const timer = window.setTimeout(() => dismiss(item.id), DURATION[item.tone])
            timers.current.set(item.id, timer)
          })
        }
        // iPhone 底部不压 Home 指示条：max() 兜住无 inset 的桌面端
        className="pointer-events-none fixed right-4 z-toast flex w-[calc(100vw-2rem)] max-w-sm flex-col gap-2 [bottom:max(1rem,env(safe-area-inset-bottom))]"
      >
        {items.map((item) => {
          const tone = TONE[item.tone]
          return (
            <div
              key={item.id}
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
