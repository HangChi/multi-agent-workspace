/** 下拉菜单：点击外部 / ESC 自动收起，用于顶栏用户菜单等。
 *
 * 键盘交互遵循 WAI-ARIA menu 模式：打开后焦点移入第一项，↑↓ 在项间移动，
 * Home/End 跳首尾，ESC 或选中后焦点归还触发按钮 —— 否则焦点一直挂在
 * trigger 上，屏幕阅读器不会念出菜单内容，键盘用户也只能盲 Tab。
 */

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { Icon } from './Icons'
import type { IconName } from './Icons'
import { cn } from './cn'

interface DropdownProps {
  trigger: ReactNode
  triggerClassName?: string
  children: ReactNode
  align?: 'start' | 'end'
  /**
   * 面板方向。侧栏底部的账号菜单必须向上弹，
   * 否则 7 天里总有那么一天它会掉出视口外。
   */
  placement?: 'bottom' | 'top'
  panelClassName?: string
  ariaLabel?: string
}

export function Dropdown({
  trigger,
  triggerClassName,
  children,
  align = 'end',
  placement = 'bottom',
  panelClassName,
  ariaLabel,
}: DropdownProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        // 焦点归还触发按钮：否则面板卸载后焦点坠回 body，键盘用户迷路
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  // 打开后焦点移入面板第一项。等一帧：面板刚挂载，同帧 focus 会被浏览器吞掉
  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => {
      panelRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [open])

  /** 菜单内方向键导航（WAI-ARIA menu 模式）：↑↓ 循环、Home/End 跳首尾 */
  const onPanelKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
    const items = Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []
    )
    if (items.length === 0) return
    e.preventDefault()
    const current = items.indexOf(document.activeElement as HTMLElement)
    const next =
      e.key === 'ArrowDown'
        ? (current + 1) % items.length
        : e.key === 'ArrowUp'
          ? (current - 1 + items.length) % items.length
          : e.key === 'Home'
            ? 0
            : items.length - 1
    items[next].focus()
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'inline-flex items-center gap-2 rounded-lg transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2',
          triggerClassName
        )}
      >
        {trigger}
      </button>
      {open && (
        <div
          ref={panelRef}
          role="menu"
          onKeyDown={onPanelKeyDown}
          onClick={() => {
            setOpen(false)
            // 选中后焦点归还 trigger，保持「从哪里来回到哪里去」
            triggerRef.current?.focus()
          }}
          className={cn(
            'absolute z-50 min-w-[12rem] rounded-xl border border-line bg-surface p-1.5 shadow-pop animate-pop-in',
            placement === 'top' ? 'bottom-full mb-2' : 'top-full mt-2',
            align === 'end' ? 'right-0' : 'left-0',
            panelClassName
          )}
        >
          {children}
        </div>
      )}
    </div>
  )
}

export function DropdownItem({
  icon,
  children,
  onClick,
  tone = 'default',
  description,
}: {
  icon?: IconName
  children: ReactNode
  onClick?: () => void
  tone?: 'default' | 'danger'
  description?: string
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={cn(
        'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors',
        tone === 'danger'
          ? 'text-rose-600 hover:bg-rose-50 dark:text-rose-300 dark:hover:bg-rose-500/15'
          : 'text-ink-soft hover:bg-surface-subtle'
      )}
    >
      {icon && <Icon name={icon} className="h-4 w-4 shrink-0 opacity-80" />}
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{children}</span>
        {description && (
          <span className="mt-0.5 block truncate text-xs font-normal text-ink-faint">
            {description}
          </span>
        )}
      </span>
    </button>
  )
}

export function DropdownSeparator() {
  return <div className="my-1 h-px bg-surface-subtle" />
}

export function DropdownLabel({ children }: { children: ReactNode }) {
  return (
    <p className="px-2.5 pb-1 pt-1.5 text-micro font-semibold uppercase tracking-wide text-ink-faint">
      {children}
    </p>
  )
}
