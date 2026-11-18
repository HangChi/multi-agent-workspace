/**
 * 通用设置：外观、账号、系统。
 *
 * 这是「设置」模块的首页，承担三件事：
 * 1. 主题切换从顶栏的一个小图标升级为可预览的选项卡片（看得见再选）；
 * 2. 把账号信息与会话说明放在一处，避免用户去翻顶栏小菜单；
 * 3. 给其他设置页一条明确的入口卡片，设置不再是一堆互不相识的页面。
 */

import { Link } from 'react-router-dom'

import { useAuth } from '../hooks/useAuth'
import { Badge } from '../components/ui/Badge'
import type { BadgeTone } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, IconTile } from '../components/ui/Card'
import { Icon } from '../components/ui/Icons'
import type { IconName } from '../components/ui/Icons'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { cn } from '../components/ui/cn'
import { useThemeStore } from '../stores/themeStore'
import type { ThemeMode } from '../stores/themeStore'
import { SETTINGS_NAV, filterNav } from '../components/layout/nav'

const ROLE_LABEL: Record<string, string> = {
  admin: '管理员',
  member: '成员',
  viewer: '只读',
}

const ROLE_TONE: Record<string, BadgeTone> = {
  admin: 'brand',
  member: 'sky',
  viewer: 'slate',
}

const ROLE_HINT: Record<string, string> = {
  admin: '可管理全部资源、查看审计日志、维护外部工具服务。',
  member: '可创建与管理自己的 Agent、需求、组与定时规则。',
  viewer: '只读账号：可浏览看板与运行结果，不能创建或修改。',
}

/** 主题预览：用纯 CSS 画一个小界面缩略图，比三个文字选项直观得多。 */
function ThemePreview({ mode }: { mode: ThemeMode }) {
  const dark = mode === 'dark'
  const auto = mode === 'system'
  return (
    <span
      aria-hidden="true"
      className={cn(
        'relative block h-[54px] w-full overflow-hidden rounded-md border',
        dark ? 'border-slate-700 bg-slate-900' : 'border-line bg-white'
      )}
    >
      <span
        className={cn('absolute inset-y-0 left-0 w-[22%]', dark ? 'bg-slate-800' : 'bg-slate-100')}
      />
      <span className="absolute left-[30%] right-3 top-2 space-y-1.5">
        <span
          className={cn('block h-1.5 w-8/12 rounded-full', dark ? 'bg-slate-600' : 'bg-slate-300')}
        />
        <span
          className={cn('block h-1.5 w-5/12 rounded-full', dark ? 'bg-slate-700' : 'bg-slate-200')}
        />
        <span className="mt-2 flex gap-1.5">
          <span
            className={cn('block h-4 flex-1 rounded', dark ? 'bg-slate-800' : 'bg-slate-100')}
          />
          <span className="block h-4 flex-1 rounded bg-brand-500/70" />
        </span>
      </span>
      {auto && (
        <span className="absolute inset-0 flex">
          <span className="h-full w-1/2 bg-slate-900/85" />
        </span>
      )}
    </span>
  )
}

const THEME_OPTIONS: { value: ThemeMode; label: string; hint: string; icon: IconName }[] = [
  { value: 'light', label: '浅色', hint: '蓝白，日间默认', icon: 'sun' },
  { value: 'dark', label: '深色', hint: '深海军蓝，夜间护眼', icon: 'moon' },
  { value: 'system', label: '跟随系统', hint: '随系统外观自动切换', icon: 'monitor' },
]

export function SettingsPage() {
  const { user, logout } = useAuth()
  const mode = useThemeStore((s) => s.mode)
  const setMode = useThemeStore((s) => s.setMode)
  const quickLinks = filterNav(SETTINGS_NAV, user?.role).filter(
    (item) => item.to !== '/settings/general'
  )

  return (
    <Page>
      <PageHeader
        eyebrow="设置"
        title="通用设置"
        description="外观、账号与系统信息。低频但又必须能找到的东西，都放在这里。"
        icon={
          <IconTile tone="brand" size="lg">
            <Icon name="settings" className="h-5 w-5" />
          </IconTile>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <Card padding="lg">
          <div className="flex items-start gap-2.5">
            <IconTile tone="slate" size="sm">
              <Icon name="palette" className="h-3.5 w-3.5" />
            </IconTile>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-ink">外观</h2>
              <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">
                主题只影响界面呈现，不改动任何数据。
              </p>
            </div>
          </div>

          <div role="radiogroup" aria-label="主题" className="mt-4 grid gap-2.5 sm:grid-cols-3">
            {THEME_OPTIONS.map((opt) => {
              const active = mode === opt.value
              return (
                <button
                  key={opt.value}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setMode(opt.value)}
                  className={cn(
                    'group rounded-xl border p-2.5 text-left transition-all duration-150',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40 focus-visible:ring-offset-1',
                    active
                      ? 'border-brand-400 bg-brand-50/50 ring-1 ring-brand-500/20'
                      : 'border-line hover:border-line-strong hover:bg-surface-muted'
                  )}
                >
                  <ThemePreview mode={opt.value} />
                  <span className="mt-2 flex items-center gap-1.5">
                    <Icon
                      name={opt.icon}
                      className={cn('h-3.5 w-3.5', active ? 'text-accent' : 'text-ink-faint')}
                    />
                    <span
                      className={cn('text-ui font-medium', active ? 'text-accent' : 'text-ink')}
                    >
                      {opt.label}
                    </span>
                    {active && <Icon name="check" className="ml-auto h-3.5 w-3.5 text-brand-500" />}
                  </span>
                  <span className="mt-0.5 block text-micro text-ink-faint">{opt.hint}</span>
                </button>
              )
            })}
          </div>
        </Card>

        <Card padding="lg">
          <div className="flex items-start gap-2.5">
            <IconTile tone="slate" size="sm">
              <Icon name="user" className="h-3.5 w-3.5" />
            </IconTile>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-ink">账号</h2>
              <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">当前会话与权限范围。</p>
            </div>
          </div>

          <div className="mt-4 flex items-center gap-3 rounded-xl bg-surface-muted p-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-sm font-semibold text-white">
              {(user?.email ?? '?').slice(0, 1).toUpperCase()}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-ui font-medium text-ink">{user?.email}</p>
              <div className="mt-1">
                <Badge tone={ROLE_TONE[user?.role ?? 'member'] ?? 'slate'} dot>
                  {ROLE_LABEL[user?.role ?? 'member'] ?? user?.role}
                </Badge>
              </div>
            </div>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-ink-muted">
            {ROLE_HINT[user?.role ?? 'member']}
          </p>

          <div className="mt-4 flex items-center justify-between gap-3 border-t border-line-subtle pt-3.5">
            <p className="text-micro leading-relaxed text-ink-faint">
              登出会撤销刷新凭证并清除浏览器 cookie。
            </p>
            <Button variant="secondary" size="sm" leftIcon="log-out" onClick={logout}>
              退出登录
            </Button>
          </div>
        </Card>
      </div>

      {quickLinks.length > 0 && (
        <section aria-labelledby="settings-more">
          <h2 id="settings-more" className="label-caps mb-2.5">
            更多设置
          </h2>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {quickLinks.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className="group flex items-start gap-3 rounded-xl border border-line/80 bg-surface p-3.5 shadow-card transition-all duration-200 ease-out-expo hover:-translate-y-0.5 hover:border-line-strong hover:shadow-panel focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40"
              >
                <IconTile tone="slate" size="md">
                  <Icon name={item.icon} className="h-4 w-4" />
                </IconTile>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1 text-ui font-medium text-ink">
                    {item.label}
                    <Icon
                      name="arrow-right"
                      className="h-3.5 w-3.5 -translate-x-1 text-ink-faint opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100"
                    />
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-ink-muted">
                    {item.description}
                  </span>
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <Card padding="lg" tone="slate">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-2.5">
            <IconTile tone="slate" size="sm">
              <Icon name="info" className="h-3.5 w-3.5" />
            </IconTile>
            <div className="min-w-0">
              <h2 className="text-sm font-semibold text-ink">系统信息</h2>
              <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">
                前端 v0.1.0 · 时区 Asia/Shanghai · 构建产物经 Nginx 提供静态服务
              </p>
            </div>
          </div>
          <Link
            to="/dashboard"
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 text-ui font-medium text-ink-soft shadow-card transition-colors hover:bg-surface-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45"
          >
            <Icon name="activity" className="h-4 w-4" />
            查看运行状态
          </Link>
        </div>
      </Card>
    </Page>
  )
}
