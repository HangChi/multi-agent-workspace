/**
 * 两组选择控件：
 * - SegmentedControl：2~4 个短选项的横向切换（类似 iOS 分段控件）
 * - OptionCards：带标题+说明的卡片式单选（路由策略、优先级等需要解释的场景）
 *
 * 都是「单选」，语义上用 aria-pressed / role=radiogroup 表达，键盘可操作。
 */

import { Icon } from './Icons'
import type { IconName } from './Icons'
import { cn } from './cn'

interface SegmentOption<T extends string> {
  value: T
  label: string
  icon?: IconName
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  className,
}: {
  options: SegmentOption<T>[]
  value: T
  onChange: (value: T) => void
  ariaLabel: string
  className?: string
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        'inline-flex items-center gap-0.5 rounded-lg border border-line bg-surface-subtle/80 p-0.5',
        className,
      )}
    >
      {options.map((opt) => {
        const active = opt.value === value
        return (
          <button
            key={opt.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(opt.value)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-[7px] px-2.5 py-1 text-xs font-medium transition-all duration-150',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40',
              active
                ? 'bg-surface text-accent shadow-sm ring-1 ring-line/60'
                : 'text-ink-muted hover:text-ink',
            )}
          >
            {opt.icon && <Icon name={opt.icon} className="h-3.5 w-3.5" />}
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

interface OptionCardItem<T extends string> {
  value: T
  label: string
  description?: string
  icon?: IconName
}

export function OptionCards<T extends string>({
  options,
  value,
  onChange,
  columns = 4,
  name,
  className,
}: {
  options: OptionCardItem<T>[]
  value: T
  onChange: (value: T) => void
  columns?: 2 | 3 | 4
  /** radiogroup 的无障碍名称 */
  name: string
  className?: string
}) {
  const grid =
    columns === 2
      ? 'sm:grid-cols-2'
      : columns === 3
        ? 'sm:grid-cols-3'
        : 'sm:grid-cols-2 lg:grid-cols-4'

  return (
    <div role="radiogroup" aria-label={name} className={cn('grid grid-cols-1 gap-2', grid, className)}>
      {options.map((opt) => {
        const active = opt.value === value
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(opt.value)}
            className={cn(
              'group relative flex items-start gap-2.5 rounded-lg border p-3 text-left transition-all duration-150',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 focus-visible:ring-offset-1',
              active
                ? 'border-brand-400 bg-brand-50/70 ring-1 ring-brand-500/20'
                : 'border-line bg-surface hover:border-line-strong hover:bg-surface-muted',
            )}
          >
            {opt.icon && (
              <span
                className={cn(
                  'mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md ring-1 ring-inset transition-colors',
                  active
                    ? 'bg-surface text-accent ring-brand-200'
                    : 'bg-surface-muted text-ink-faint ring-line/70 group-hover:text-ink-soft',
                )}
              >
                <Icon name={opt.icon} className="h-3.5 w-3.5" />
              </span>
            )}
            <span className="min-w-0 flex-1">
              <span
                className={cn(
                  'block text-sm font-medium',
                  active ? 'text-accent' : 'text-ink',
                )}
              >
                {opt.label}
              </span>
              {opt.description && (
                <span className="mt-0.5 block text-xs leading-relaxed text-ink-muted">
                  {opt.description}
                </span>
              )}
            </span>
            {active && (
              <Icon
                name="check-circle"
                className="absolute right-2 top-2 h-4 w-4 text-brand-500 animate-fade-in"
              />
            )}
          </button>
        )
      })}
    </div>
  )
}
