/**
 * 弹层外壳：遮罩 + 居中面板（移动端自动变底部抽屉）。
 *
 * 细节：ESC 关闭、点击遮罩关闭、打开时锁定页面滚动、右上角关闭按钮、
 * 打开后焦点落到面板，保证键盘用户不会「丢焦点」。
 *
 * 焦点陷阱（WCAG 2.1 SC 2.1.2）：Tab / Shift+Tab 在面板内循环，不泄漏到
 * 背景页面 —— 读屏用户顺着焦点走，否则会「走出对话框还在念背后的内容」。
 * 关闭时焦点归还触发元素，避免下次 Tab 从文档开头开始。
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

  // ESC 关闭 + 背景滚动锁定 + 焦点陷阱 + 焦点归还
  useEffect(() => {
    if (!open) return

    const FOCUSABLE =
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    /** 打开前的焦点持有者：关闭时还回去，避免下次 Tab 从文档开头开始 */
    const previouslyFocused = document.activeElement as HTMLElement | null

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
        return
      }
      if (e.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el.getClientRects().length > 0
      )
      if (focusable.length === 0) {
        e.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      const inside = panel.contains(active)
      if (!e.shiftKey && (!inside || active === last)) {
        e.preventDefault()
        first.focus()
      } else if (e.shiftKey && (!inside || active === first || active === panel)) {
        e.preventDefault()
        last.focus()
      }
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
      // 焦点归还：仅当触发元素仍在文档中才还，避免抢走用户已主动转移的焦点
      if (previouslyFocused?.isConnected) previouslyFocused.focus()
    }
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-modal flex items-end justify-center overflow-y-auto bg-slate-900/45 p-0 backdrop-blur-[2px] animate-fade-in sm:items-center sm:p-6"
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
