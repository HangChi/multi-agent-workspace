/**
 * 表单控件族：Input / Textarea / Select / NumberInput / Slider。
 *
 * 设计约定（全站一致，避免各页面手写边框色）：
 * - 默认：slate-300 描边 + 内阴影
 * - hover：描边加深一档
 * - focus：品牌色描边 + 4px 柔光环（不是生硬的浏览器 outline）
 * - error：玫红描边 + 玫红柔光环，并提供 role=alert 的错误文案
 * - 每个控件都可直接传 label / hint / error，内部自动包裹并生成 id
 */

import { forwardRef, useId } from 'react'
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'

import { Icon } from './Icons'
import type { IconName } from './Icons'
import { cn } from './cn'

interface FieldExtras {
  label?: ReactNode
  /** 控件下方的常驻说明 */
  hint?: ReactNode
  /** 控件下方的错误（同时把控件标红） */
  error?: string | null
  required?: boolean
  /** 标签右侧的辅助信息（字数统计、单位、当前值等） */
  labelExtra?: ReactNode
  /** 外层包裹容器样式（宽度/栅格用） */
  wrapperClassName?: string
}

const CONTROL_BASE =
  'w-full rounded-lg border bg-surface text-sm text-ink shadow-field outline-none ' +
  'transition-[border-color,box-shadow,background-color] duration-150 ' +
  'placeholder:text-ink-faint disabled:cursor-not-allowed disabled:border-line ' +
  'disabled:bg-surface-muted disabled:text-ink-faint disabled:shadow-none'

function controlTone(error?: string | null) {
  return error
    ? 'border-rose-300 bg-rose-50/40 focus:border-rose-500 focus:ring-4 focus:ring-rose-500/10 dark:border-rose-500/40 dark:bg-rose-500/10 dark:focus:ring-rose-500/15'
    : 'border-line-strong hover:border-ink-faint focus:border-brand-500 focus:ring-4 focus:ring-brand-500/10'
}

const HEIGHTS = {
  sm: 'h-9 px-2.5 text-ui',
  md: 'h-10 px-3',
} as const

function FieldShell({
  id,
  label,
  labelExtra,
  hint,
  error,
  required,
  className,
  children,
}: {
  id: string
  label?: ReactNode
  labelExtra?: ReactNode
  hint?: ReactNode
  error?: string | null
  required?: boolean
  className?: string
  children: ReactNode
}) {
  const message = error ? (
    <p
      id={`${id}-msg`}
      role="alert"
      className="mt-1.5 flex items-start gap-1 text-xs leading-relaxed text-rose-600"
    >
      <Icon name="alert-circle" className="mt-px h-3.5 w-3.5 shrink-0" />
      <span>{error}</span>
    </p>
  ) : hint ? (
    <p id={`${id}-msg`} className="mt-1.5 text-xs leading-relaxed text-ink-muted">
      {hint}
    </p>
  ) : null

  return (
    <div className={className}>
      {(label || labelExtra) && (
        <div className="mb-1.5 flex items-baseline justify-between gap-2">
          {label ? (
            <label htmlFor={id} className="text-ui font-medium text-ink-soft">
              {label}
              {required && (
                <span className="ml-0.5 text-rose-500" aria-hidden="true">
                  *
                </span>
              )}
            </label>
          ) : (
            <span />
          )}
          {labelExtra && (
            <span className="shrink-0 text-micro tabular-nums text-ink-faint">{labelExtra}</span>
          )}
        </div>
      )}
      {children}
      {message}
    </div>
  )
}

// ---------------------------------------------------------------- Input

export interface InputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'>, FieldExtras {
  controlSize?: 'sm' | 'md'
  leftIcon?: IconName
  /** 右侧附加（清除按钮、单位、显隐切换等） */
  rightSlot?: ReactNode
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    label,
    hint,
    error,
    required,
    labelExtra,
    wrapperClassName,
    controlSize = 'md',
    leftIcon,
    rightSlot,
    className,
    id,
    ...rest
  },
  ref
) {
  const autoId = useId()
  const inputId = id ?? autoId

  return (
    <FieldShell
      id={inputId}
      label={label}
      labelExtra={labelExtra}
      hint={hint}
      error={error}
      required={required}
      className={wrapperClassName}
    >
      <div className="relative">
        {leftIcon && (
          <Icon
            name={leftIcon}
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint"
          />
        )}
        <input
          ref={ref}
          id={inputId}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={error || hint ? `${inputId}-msg` : undefined}
          className={cn(
            CONTROL_BASE,
            HEIGHTS[controlSize],
            controlTone(error),
            leftIcon && 'pl-9',
            rightSlot ? 'pr-10' : null,
            className
          )}
          {...rest}
        />
        {rightSlot && (
          <div className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center">
            {rightSlot}
          </div>
        )}
      </div>
    </FieldShell>
  )
})

// -------------------------------------------------------------- Textarea

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement>, FieldExtras {
  /** 右下角字数统计 */
  showCount?: boolean
  maxLength?: number
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  {
    label,
    hint,
    error,
    required,
    labelExtra,
    wrapperClassName,
    showCount,
    maxLength,
    className,
    id,
    value,
    ...rest
  },
  ref
) {
  const autoId = useId()
  const fieldId = id ?? autoId
  const length = typeof value === 'string' ? value.length : 0

  return (
    <FieldShell
      id={fieldId}
      label={label}
      labelExtra={
        showCount ? (
          <span>
            {length}
            {maxLength ? ` / ${maxLength}` : ''}
          </span>
        ) : (
          labelExtra
        )
      }
      hint={hint}
      error={error}
      required={required}
      className={wrapperClassName}
    >
      <textarea
        ref={ref}
        id={fieldId}
        required={required}
        maxLength={maxLength}
        value={value}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || hint ? `${fieldId}-msg` : undefined}
        className={cn(
          CONTROL_BASE,
          'min-h-[88px] resize-y px-3 py-2.5 leading-relaxed',
          controlTone(error),
          className
        )}
        {...rest}
      />
    </FieldShell>
  )
})

// ---------------------------------------------------------------- Select

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement>, FieldExtras {
  controlSize?: 'sm' | 'md'
  /** 作为首项的空占位（不可选） */
  placeholder?: string
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  {
    label,
    hint,
    error,
    required,
    labelExtra,
    wrapperClassName,
    controlSize = 'md',
    placeholder,
    className,
    id,
    children,
    ...rest
  },
  ref
) {
  const autoId = useId()
  const fieldId = id ?? autoId

  return (
    <FieldShell
      id={fieldId}
      label={label}
      labelExtra={labelExtra}
      hint={hint}
      error={error}
      required={required}
      className={wrapperClassName}
    >
      <div className="relative">
        <select
          ref={ref}
          id={fieldId}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={error || hint ? `${fieldId}-msg` : undefined}
          className={cn(
            CONTROL_BASE,
            HEIGHTS[controlSize],
            'cursor-pointer appearance-none pr-9',
            controlTone(error),
            className
          )}
          {...rest}
        >
          {placeholder && (
            <option value="" disabled>
              {placeholder}
            </option>
          )}
          {children}
        </select>
        <Icon
          name="chevron-down"
          className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint"
        />
      </div>
    </FieldShell>
  )
})

// ----------------------------------------------------------- NumberInput

export interface NumberInputProps
  extends
    Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type' | 'size'>,
    FieldExtras {
  value: number
  onValueChange: (value: number) => void
  min?: number
  max?: number
  step?: number
  /** 数值单位（秒 / 分 / 步） */
  suffix?: string
}

/** 自绘步进数字输入：隐藏原生小箭头，改用右侧 −/+ 按钮，点击区域更大。 */
export function NumberInput({
  value,
  onValueChange,
  min,
  max,
  step = 1,
  suffix,
  label,
  hint,
  error,
  required,
  labelExtra,
  wrapperClassName,
  className,
  id,
  disabled,
  ...rest
}: NumberInputProps) {
  const autoId = useId()
  const fieldId = id ?? autoId

  /** 收敛到合法区间，并消掉浮点累加误差（0.1 步长会算出 0.30000000000000004） */
  const clamp = (next: number) => {
    if (Number.isNaN(next)) return min ?? 0
    let v = next
    if (min !== undefined) v = Math.max(min, v)
    if (max !== undefined) v = Math.min(max, v)
    return Math.round(v * 1000) / 1000
  }

  const canDecrease = min === undefined || value > min
  const canIncrease = max === undefined || value < max

  return (
    <FieldShell
      id={fieldId}
      label={label}
      labelExtra={labelExtra}
      hint={hint}
      error={error}
      required={required}
      className={wrapperClassName}
    >
      <div
        className={cn(
          'flex h-10 w-full items-stretch overflow-hidden rounded-lg border bg-surface shadow-field',
          'transition-[border-color,box-shadow] duration-150 focus-within:ring-4',
          error
            ? 'border-rose-300 focus-within:border-rose-500 focus-within:ring-rose-500/10'
            : 'border-line-strong hover:border-line-strong focus-within:border-brand-500 focus-within:ring-brand-500/10',
          disabled && 'cursor-not-allowed border-line bg-surface-muted shadow-none',
          className
        )}
      >
        <button
          type="button"
          aria-label="减少"
          tabIndex={-1}
          disabled={disabled || !canDecrease}
          onClick={() => onValueChange(clamp(value - step))}
          className="flex w-9 shrink-0 items-center justify-center border-r border-line/80 text-ink-muted transition-colors hover:bg-surface-muted hover:text-ink disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <Icon name="minus" className="h-3.5 w-3.5" />
        </button>
        <input
          id={fieldId}
          type="number"
          inputMode="decimal"
          value={Number.isFinite(value) ? value : ''}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={error || hint ? `${fieldId}-msg` : undefined}
          onChange={(e) =>
            onValueChange(clamp(e.target.value === '' ? (min ?? 0) : Number(e.target.value)))
          }
          className="min-w-0 flex-1 border-0 bg-transparent text-center text-sm tabular-nums text-ink outline-none focus:ring-0 disabled:bg-transparent"
          {...rest}
        />
        {suffix && <span className="flex items-center pr-1 text-xs text-ink-faint">{suffix}</span>}
        <button
          type="button"
          aria-label="增加"
          tabIndex={-1}
          disabled={disabled || !canIncrease}
          onClick={() => onValueChange(clamp(value + step))}
          className="flex w-9 shrink-0 items-center justify-center border-l border-line/80 text-ink-muted transition-colors hover:bg-surface-muted hover:text-ink disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <Icon name="plus" className="h-3.5 w-3.5" />
        </button>
      </div>
    </FieldShell>
  )
}

// ---------------------------------------------------------------- Slider

export interface SliderProps {
  id?: string
  label?: ReactNode
  hint?: ReactNode
  value: number
  onValueChange: (value: number) => void
  min?: number
  max?: number
  step?: number
  /** 右上角展示的当前值（如 "0.7"） */
  displayValue?: ReactNode
  wrapperClassName?: string
}

/** 强调「连续可调」的参数（温度）用滑杆，比纯数字框更直观。 */
export function Slider({
  id,
  label,
  hint,
  value,
  onValueChange,
  min = 0,
  max = 1,
  step = 0.1,
  displayValue,
  wrapperClassName,
}: SliderProps) {
  const autoId = useId()
  const fieldId = id ?? autoId
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0

  return (
    <div className={wrapperClassName}>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        {label ? (
          <label htmlFor={fieldId} className="text-ui font-medium text-ink-soft">
            {label}
          </label>
        ) : (
          <span />
        )}
        <span className="rounded-md bg-surface-subtle px-1.5 py-0.5 text-xs font-medium tabular-nums text-ink-soft">
          {displayValue ?? value}
        </span>
      </div>
      <input
        id={fieldId}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onValueChange(Number(e.target.value))}
        style={{
          background: `linear-gradient(to right, rgb(var(--brand-600)) ${pct}%, rgb(var(--line-strong)) ${pct}%)`,
        }}
        className="range-brand"
      />
      <div className="mt-1 flex justify-between text-micro text-ink-faint">
        <span>{min}</span>
        <span>{max}</span>
      </div>
      {hint && <p className="mt-1 text-xs leading-relaxed text-ink-muted">{hint}</p>}
    </div>
  )
}
