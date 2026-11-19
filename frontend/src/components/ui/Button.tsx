/**
 * 按钮体系：8 种语义变体 × 4 档尺寸，内置 loading / 图标 / 全宽。
 *
 * as="link" 时渲染为 react-router <Link>：跳转型操作（空状态 CTA、
 * 「查看全部」）用 button 语义是错的，但视觉上要与按钮完全一致。
 *
 * 统一的交互反馈：hover 提亮 → active 加深 → focus-visible 描边环 → disabled 降透明度。
 */

import { forwardRef } from 'react'
import type { ButtonHTMLAttributes } from 'react'
import { Link } from 'react-router-dom'

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
  // 渐变底 + 品牌光晕：实心蓝太平，加一层从 brand-500 到 brand-600 的
  // 微渐变和彩色投影，按钮立刻有「质感」（对齐 Stripe / Linear 的主按钮）
  primary:
    'border border-brand-700/25 bg-gradient-to-b from-brand-500 to-brand-600 text-white shadow-glow hover:from-brand-600 hover:to-brand-700 hover:shadow-lift active:from-brand-700 active:to-brand-800',
  secondary:
    'border border-line-strong bg-surface text-ink-soft shadow-card hover:border-line-strong hover:bg-surface-muted active:bg-surface-subtle',
  outline:
    'border border-brand-200 bg-brand-50/70 text-accent hover:border-brand-300 hover:bg-brand-50 active:bg-brand-100',
  ghost:
    'border border-transparent text-ink-soft hover:bg-surface-subtle hover:text-ink active:bg-line/70',
  danger:
    'border border-rose-700/25 bg-gradient-to-b from-rose-500 to-rose-600 text-white shadow-sm hover:from-rose-600 hover:to-rose-700 active:from-rose-700 active:to-rose-800',
  success:
    'border border-emerald-700/25 bg-gradient-to-b from-emerald-500 to-emerald-600 text-white shadow-sm hover:from-emerald-600 hover:to-emerald-700 active:from-emerald-700 active:to-emerald-800',
  warning:
    'border border-amber-600/25 bg-gradient-to-b from-amber-400 to-amber-500 text-white shadow-sm hover:from-amber-500 hover:to-amber-600 active:from-amber-600 active:to-amber-700',
  link: 'border border-transparent px-0 text-accent underline-offset-4 hover:text-accent hover:underline',
}

const SIZES: Record<ButtonSize, string> = {
  xs: 'h-7 rounded-md px-2 text-xs',
  // 36px 是「鼠标友好 + 触屏可点」的折中下限；移动端主操作一律用 md/lg（40/44px）
  sm: 'h-9 rounded-lg px-3 text-ui',
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
  /** 渲染为路由链接而非按钮（跳转型操作）；须同时传 to */
  as?: 'button' | 'link'
  to?: string
  /** 危险色调（配 variant="ghost"）：列表行内的删除/移除入口。
   *  此前各组页/模板页手抄一整套 rose 类，收口到这里。 */
  tone?: 'default' | 'danger'
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'md',
    loading = false,
    leftIcon,
    rightIcon,
    fullWidth,
    as = 'button',
    to,
    tone = 'default',
    className,
    children,
    disabled,
    type = 'button',
    ...rest
  },
  ref
) {
  const dangerGhost =
    tone === 'danger'
      ? 'text-rose-700 hover:bg-rose-50 hover:text-rose-800 dark:text-rose-300 dark:hover:bg-rose-500/15'
      : null
  const classes = cn(
    BASE,
    VARIANTS[variant],
    SIZES[size],
    dangerGhost,
    fullWidth && 'w-full',
    className
  )

  // 跳转型按钮：Link 渲染，视觉与交互（含 focus 环）与 button 完全一致
  if (as === 'link' && to) {
    return (
      <Link to={to} className={classes}>
        {leftIcon && <Icon name={leftIcon} className={ICON_SIZES[size]} />}
        {children}
        {rightIcon && <Icon name={rightIcon} className={ICON_SIZES[size]} />}
      </Link>
    )
  }

  return (
    <button ref={ref} type={type} disabled={disabled || loading} className={classes} {...rest}>
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
