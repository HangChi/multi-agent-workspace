/** 主题切换：浅色 / 深色 / 跟随系统（下拉菜单，当前项打勾）。 */

import { Icon } from './Icons'
import type { IconName } from './Icons'
import { Dropdown } from './Dropdown'
import { useThemeStore } from '../../stores/themeStore'
import type { ThemeMode } from '../../stores/themeStore'
import { cn } from './cn'

const MODES: { value: ThemeMode; label: string; hint: string; icon: IconName }[] = [
  { value: 'light', label: '浅色', hint: '蓝白配色', icon: 'sun' },
  { value: 'dark', label: '深色', hint: '夜间低亮度', icon: 'moon' },
  { value: 'system', label: '跟随系统', hint: '随系统外观切换', icon: 'monitor' },
]

export function ThemeToggle({ className }: { className?: string }) {
  const mode = useThemeStore((s) => s.mode)
  const setMode = useThemeStore((s) => s.setMode)
  const current = MODES.find((m) => m.value === mode) ?? MODES[2]

  return (
    <Dropdown
      ariaLabel={`主题：${current.label}`}
      panelClassName="w-44"
      triggerClassName={cn(
        'h-8 w-8 justify-center border border-line bg-surface text-ink-muted hover:bg-surface-subtle hover:text-ink',
        className,
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
                : 'text-ink-soft hover:bg-surface-subtle',
            )}
          >
            <Icon name={m.icon} className="h-4 w-4 shrink-0 opacity-80" />
            <span className="min-w-0 flex-1">
              <span className="block truncate">{m.label}</span>
              <span className="mt-0.5 block truncate text-2xs font-normal text-ink-faint">
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
