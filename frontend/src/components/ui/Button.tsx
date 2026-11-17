/**
 * 按钮体系：8 种语义变体 × 4 档尺寸，内置 loading / 图标 / 全宽。
 *
 * 统一的交互反馈：hover 提亮 → active 加深 → focus-visible 描边环 → disabled 降透明度。
 */

import { forwardRef } from 'react'
import type { ButtonHTMLAttributes } from 'react'

import { Icon } from './Icons'
import type { IconName } from './Icons'
import { Spinner } from './Spinner'
import { cn } from './cn'

export type ButtonVariant =
  'primary' | 'secondary' | 'outline' | 'ghost' | 'danger' | 'success' | 'warning' | 'link'

export type ButtonSize = 'xs' | 'sm' | 'md' | 'lg'

const BASE =
  'inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap font-medium ' +
  'transition-[color,background-color,border-color,box-shadow,opacity] duration-150 ease-out ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 ' +
  'focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:cursor-not-allowed disabled:opacity-50'

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'border border-brand-700/20 bg-brand-600 text-white shadow-btn-primary hover:bg-brand-700 active:bg-brand-800',
  secondary:
    'border border-line-strong bg-surface text-ink-soft shadow-card hover:border-line-strong hover:bg-surface-muted active:bg-surface-subtle',
  outline:
    'border border-brand-200 bg-brand-50/70 text-accent hover:border-brand-300 hover:bg-brand-50 active:bg-brand-100',
  ghost:
    'border border-transparent text-ink-soft hover:bg-surface-subtle hover:text-ink active:bg-line/70',
  danger:
    'border border-rose-700/20 bg-rose-600 text-white shadow-sm hover:bg-rose-700 active:bg-rose-800',
  success:
    'border border-emerald-700/20 bg-emerald-600 text-white shadow-sm hover:bg-emerald-700 active:bg-emerald-800',
  warning:
    'border border-amber-600/20 bg-amber-500 text-white shadow-sm hover:bg-amber-600 active:bg-amber-700',
  link: 'border border-transparent px-0 text-accent underline-offset-4 hover:text-accent hover:underline',
}

const SIZES: Record<ButtonSize, string> = {
  xs: 'h-7 rounded-md px-2 text-xs',
  sm: 'h-8 rounded-lg px-2.5 text-[13px]',
  md: 'h-10 rounded-lg px-4 text-sm',
  lg: 'h-11 rounded-xl px-5 text-sm',
}

const ICON_SIZES: Record<ButtonSize, string> = {
  xs: 'h-3.5 w-3.5',
  sm: 'h-3.5 w-3.5',
  md: 'h-4 w-4',
  lg: 'h-[18px] w-[18px]',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** 显示加载态：替换左侧图标为转圈，并自动禁用 */
  loading?: boolean
  leftIcon?: IconName
  rightIcon?: IconName
  fullWidth?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading = false,
    leftIcon,
    rightIcon,
    fullWidth,
    className,
    children,
    disabled,
    type = 'button',
    ...rest
  },
  ref
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={cn(BASE, VARIANTS[variant], SIZES[size], fullWidth && 'w-full', className)}
      {...rest}
    >
      {loading ? (
        <Spinner className={ICON_SIZES[size]} />
      ) : leftIcon ? (
        <Icon name={leftIcon} className={ICON_SIZES[size]} />
      ) : null}
      {children}
      {rightIcon && !loading && <Icon name={rightIcon} className={ICON_SIZES[size]} />}
    </button>
  )
})

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: IconName
  /** 必填：图标按钮没有文字，必须给无障碍名称 */
  label: string
  variant?: 'ghost' | 'secondary' | 'danger'
  size?: 'xs' | 'sm' | 'md'
}

const ICON_BTN_SIZES = {
  xs: 'h-6 w-6 rounded-md',
  sm: 'h-8 w-8 rounded-lg',
  md: 'h-9 w-9 rounded-lg',
} as const

const ICON_BTN_VARIANTS = {
  ghost: 'text-ink-muted hover:bg-surface-subtle hover:text-ink',
  secondary:
    'border border-line-strong bg-surface text-ink-muted hover:border-ink-faint hover:text-ink',
  danger:
    'text-ink-muted hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/15 dark:hover:text-rose-300',
} as const

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, variant = 'ghost', size = 'sm', className, type = 'button', ...rest },
  ref
) {
  const iconSize = size === 'xs' ? 'h-3.5 w-3.5' : 'h-4 w-4'
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex shrink-0 items-center justify-center transition-colors duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-40',
        ICON_BTN_SIZES[size],
        ICON_BTN_VARIANTS[variant],
        className
      )}
      {...rest}
    >
      <Icon name={icon} className={iconSize} />
    </button>
  )
})
