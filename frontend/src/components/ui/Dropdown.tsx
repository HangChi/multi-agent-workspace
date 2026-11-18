/** 下拉菜单：点击外部 / ESC 自动收起，用于顶栏用户菜单等。 */

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

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className="relative" ref={rootRef}>
      <button
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
          role="menu"
          onClick={() => setOpen(false)}
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
    <p className="px-2.5 pb-1 pt-1.5 text-2xs font-semibold uppercase tracking-wide text-ink-faint">
      {children}
    </p>
  )
}
