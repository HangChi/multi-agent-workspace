/**
 * 弹层外壳：遮罩 + 居中面板（移动端自动变底部抽屉）。
 *
 * 细节：ESC 关闭、点击遮罩关闭、打开时锁定页面滚动、右上角关闭按钮、
 * 打开后焦点落到面板，保证键盘用户不会「丢焦点」。
 */

import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'

import { IconButton } from './Button'
import { cn } from './cn'

const SIZES = {
  sm: 'sm:max-w-sm',
  md: 'sm:max-w-md',
  lg: 'sm:max-w-lg',
  xl: 'sm:max-w-2xl',
  '2xl': 'sm:max-w-4xl',
} as const

export interface ModalProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  description?: ReactNode
  /** 标题左侧的图标底板 */
  icon?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  size?: keyof typeof SIZES
}

export function Modal({
  open,
  onClose,
  title,
  description,
  icon,
  children,
  footer,
  size = 'md',
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const titleId = useId()

  // ESC 关闭 + 背景滚动锁定
  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    // 焦点进入面板，避免读屏停留在背后的页面
    const timer = window.setTimeout(() => panelRef.current?.focus(), 0)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = prevOverflow
      window.clearTimeout(timer)
    }
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center overflow-y-auto bg-slate-900/45 p-0 backdrop-blur-[2px] animate-fade-in sm:items-center sm:p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      role="presentation"
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        tabIndex={-1}
        className={cn(
          'relative w-full rounded-t-2xl bg-surface shadow-pop outline-none ring-1 ring-line/60 animate-pop-in',
          'max-h-[92vh] overflow-y-auto sm:rounded-2xl',
          SIZES[size]
        )}
      >
        {(title || description) && (
          <div className="flex items-start gap-3 px-5 pb-4 pt-5 sm:px-6 sm:pt-6">
            {icon}
            <div className="min-w-0 flex-1 pr-8">
              {title && (
                <h2 id={titleId} className="text-base font-semibold text-ink">
                  {title}
                </h2>
              )}
              {description && (
                <p className="mt-1 text-sm leading-relaxed text-ink-muted">{description}</p>
              )}
            </div>
            <IconButton
              icon="x"
              label="关闭"
              size="sm"
              onClick={onClose}
              className="absolute right-3.5 top-3.5"
            />
          </div>
        )}

        {children && <div className="px-5 pb-5 sm:px-6">{children}</div>}

        {footer && (
          <div className="flex items-center justify-end gap-2 rounded-b-2xl border-t border-line-subtle bg-surface-muted/70 px-5 py-3.5 sm:px-6">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body
  )
}
