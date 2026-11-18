/**
 * 主题控件，两种形态：
 * - `menu`（默认）：浅色 / 深色 / 跟随系统 三选一下拉，登录页用；
 * - `toggle`：一键明暗切换，工作台顶栏用。
 *
 * 为什么顶栏用 toggle：主题的完整选择（含跟随系统）已经放进
 * 设置 → 通用设置 → 外观，顶栏只承担「我现在想换个亮度」这一件事，
 * 一个按钮点一下就够，不该再弹一层菜单。
 *
 * 从「跟随系统」一键切过来时会落成明确的 light/dark，
 * 这是用户意图的显式表达，比继续跟随更符合预期。
 */

import { Icon } from './Icons'
import type { IconName } from './Icons'
import { Dropdown } from './Dropdown'
import { resolveDark, useThemeStore } from '../../stores/themeStore'
import type { ThemeMode } from '../../stores/themeStore'
import { cn } from './cn'

const MODES: { value: ThemeMode; label: string; hint: string; icon: IconName }[] = [
  { value: 'light', label: '浅色', hint: '蓝白配色', icon: 'sun' },
  { value: 'dark', label: '深色', hint: '夜间低亮度', icon: 'moon' },
  { value: 'system', label: '跟随系统', hint: '随系统外观自动切换', icon: 'monitor' },
]

interface Props {
  className?: string
  variant?: 'menu' | 'toggle'
}

export function ThemeToggle({ className, variant = 'menu' }: Props) {
  const mode = useThemeStore((s) => s.mode)
  const setMode = useThemeStore((s) => s.setMode)

  if (variant === 'toggle') {
    const dark = resolveDark(mode)
    return (
      <button
        type="button"
        onClick={() => setMode(dark ? 'light' : 'dark')}
        aria-label={dark ? '切换到浅色' : '切换到深色'}
        title={dark ? '切换到浅色' : '切换到深色'}
        className={cn(
          'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-ink-muted transition-colors',
          'hover:bg-surface-subtle hover:text-ink',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45 focus-visible:ring-offset-2',
          className
        )}
      >
        <Icon name={dark ? 'sun' : 'moon'} className="h-[17px] w-[17px]" />
      </button>
    )
  }

  const current = MODES.find((m) => m.value === mode) ?? MODES[2]

  return (
    <Dropdown
      ariaLabel={`主题：${current.label}`}
      panelClassName="w-44"
      triggerClassName={cn(
        'h-8 w-8 justify-center border border-line bg-surface text-ink-muted hover:bg-surface-subtle hover:text-ink',
        className
      )}
      trigger={<Icon name={current.icon} className="h-4 w-4" />}
    >
      {MODES.map((m) => {
        const active = m.value === mode
        return (
          <button
            key={m.value}
            type="button"
            role="menuitemradio"
            aria-checked={active}
            onClick={() => setMode(m.value)}
            className={cn(
              'flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors',
              active
                ? 'bg-brand-50 font-medium text-accent'
                : 'text-ink-soft hover:bg-surface-subtle'
            )}
          >
            <Icon name={m.icon} className="h-4 w-4 shrink-0 opacity-80" />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{m.label}</span>
              <span className="mt-0.5 block truncate text-micro font-normal text-ink-faint">
                {m.hint}
              </span>
            </span>
            {active && <Icon name="check" className="h-3.5 w-3.5 shrink-0" />}
          </button>
        )
      })}
    </Dropdown>
  )
}
