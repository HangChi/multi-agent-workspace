/**
 * 规则列表（T051）：每条规则的启停、下次/上次触发、最近结果与操作。
 *
 * 「下次触发」与「最近结果」并排展示是有意的：只给下次时间，用户看不出
 * 这条规则昨天到底有没有跑；只给最近结果，又不知道它还会不会继续跑。
 *
 * 目标状态为 `archived`/`missing` 时必须显式标出来 —— 规则本身看起来正常，
 * 到点却必然 `failed`，不提示的话用户要等到看历史才发现。
 */

import type { Schedule, ScheduleTriggerResult } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Icon } from '../ui/Icons'
import { cn } from '../ui/cn'

const RESULT_LABEL: Record<ScheduleTriggerResult, string> = {
  fired: '成功',
  missed: '错过',
  skipped_busy: '跳过',
  failed: '失败',
}

const RESULT_TONE: Record<ScheduleTriggerResult, BadgeTone> = {
  fired: 'emerald',
  missed: 'amber',
  skipped_busy: 'sky',
  failed: 'rose',
}

const TARGET_TYPE_LABEL: Record<string, string> = {
  agent: 'Agent',
  group: '组',
}

const STRATEGY_LABEL: Record<string, string> = {
  direct: '直接',
  fan_out: '并行',
  pipeline: '串行',
  router: '路由',
}

function formatDateTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 距离下次触发的相对时间，让人不必自己算。 */
function relativeToNow(value: string | null): string | null {
  if (!value) return null
  const diffMs = new Date(value).getTime() - Date.now()
  if (Number.isNaN(diffMs)) return null
  const abs = Math.abs(diffMs)
  const minutes = Math.round(abs / 60000)
  if (minutes < 1) return '即将触发'
  const text =
    minutes < 60
      ? `${minutes} 分钟`
      : minutes < 60 * 24
        ? `${Math.round(minutes / 60)} 小时`
        : `${Math.round(minutes / 1440)} 天`
  return diffMs >= 0 ? `${text}后` : `${text}前`
}

interface Props {
  schedule: Schedule
  /** 当前用户是否可写（viewer 只读、admin 不可写他人）。 */
  canWrite: boolean
  onToggleEnabled: (schedule: Schedule, enabled: boolean) => void
  onEdit: (schedule: Schedule) => void
  onDelete: (schedule: Schedule) => void
  onShowHistory: (schedule: Schedule) => void
  toggling?: boolean
}

export function ScheduleList({
  schedule,
  canWrite,
  onToggleEnabled,
  onEdit,
  onDelete,
  onShowHistory,
  toggling,
}: Props) {
  const target = schedule.target
  const targetBroken = target.status !== 'active'

  return (
    <div
      className={cn(
        'rounded-xl border bg-surface p-4 shadow-card transition-colors',
        schedule.enabled ? 'border-line/90' : 'border-line-subtle bg-surface-muted/40'
      )}
    >
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3
              className={cn(
                'truncate text-sm font-semibold',
                schedule.enabled ? 'text-ink' : 'text-ink-muted'
              )}
            >
              {schedule.name}
            </h3>
            {schedule.enabled ? (
              <Badge tone="emerald" dot>
                启用中
              </Badge>
            ) : (
              <Badge tone="slate" dot>
                已停用
              </Badge>
            )}
            {schedule.last_result && (
              <Badge tone={RESULT_TONE[schedule.last_result]}>
                最近：{RESULT_LABEL[schedule.last_result]}
              </Badge>
            )}
            {schedule.owner_email && (
              <span className="text-2xs text-ink-faint">{schedule.owner_email}</span>
            )}
          </div>

          {schedule.description && (
            <p className="mt-1 line-clamp-1 text-xs leading-relaxed text-ink-muted">
              {schedule.description}
            </p>
          )}

          <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-muted">
            <span className="inline-flex items-center gap-1">
              <Icon name="clock" className="h-3 w-3 text-ink-faint" />
              <code className="rounded bg-surface-subtle px-1 py-0.5 font-mono text-2xs text-ink-soft">
                {schedule.cron_expr}
              </code>
              <span className="text-ink-faint">{schedule.timezone}</span>
            </span>

            <span className="inline-flex items-center gap-1">
              <Icon name="target" className="h-3 w-3 text-ink-faint" />
              <span>
                {TARGET_TYPE_LABEL[target.type] ?? target.type}
                {' · '}
                <span className="font-medium text-ink-soft">{target.name ?? '（已删除）'}</span>
                {target.type === 'group' && target.strategy && (
                  <span className="text-ink-faint">
                    {' '}
                    / {STRATEGY_LABEL[target.strategy] ?? target.strategy}
                  </span>
                )}
              </span>
            </span>

            {schedule.enabled && (
              <span className="inline-flex items-center gap-1">
                <Icon name="activity" className="h-3 w-3 text-ink-faint" />
                下次 {formatDateTime(schedule.next_run_at)}
                {relativeToNow(schedule.next_run_at) && (
                  <span className="text-ink-faint">（{relativeToNow(schedule.next_run_at)}）</span>
                )}
              </span>
            )}

            <span className="text-ink-faint">上次 {formatDateTime(schedule.last_run_at)}</span>
          </div>

          {/* 目标失效：规则看起来正常，到点却必然失败 */}
          {targetBroken && (
            <p className="mt-2 flex items-start gap-1.5 rounded-md bg-amber-50 px-2 py-1.5 text-xs leading-relaxed text-amber-800">
              <Icon name="alert-triangle" className="mt-px h-3.5 w-3.5 shrink-0" />
              {target.status === 'archived'
                ? '执行目标已归档，该规则到点会失败。请编辑并选择新的目标，或停用此规则。'
                : '执行目标已被删除，该规则到点会失败。请编辑并选择新的目标，或停用此规则。'}
            </p>
          )}

          {/* 自动停用：必须解释为什么，否则用户会以为规则自己坏了 */}
          {schedule.auto_disabled_reason && (
            <p className="mt-2 flex items-start gap-1.5 rounded-md bg-rose-50 px-2 py-1.5 text-xs leading-relaxed text-rose-700">
              <Icon name="x-circle" className="mt-px h-3.5 w-3.5 shrink-0" />
              连续失败 {schedule.consecutive_failures} 次后已自动停用：
              {schedule.auto_disabled_reason}
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            leftIcon="clock"
            onClick={() => onShowHistory(schedule)}
          >
            历史
          </Button>

          {canWrite && (
            <>
              <Button
                size="sm"
                variant={schedule.enabled ? 'ghost' : 'outline'}
                leftIcon={schedule.enabled ? 'eye-off' : 'play'}
                loading={toggling}
                onClick={() => onToggleEnabled(schedule, !schedule.enabled)}
              >
                {schedule.enabled ? '停用' : '启用'}
              </Button>
              <Button size="sm" variant="ghost" leftIcon="edit" onClick={() => onEdit(schedule)}>
                编辑
              </Button>
              <Button
                size="sm"
                variant="ghost"
                leftIcon="trash"
                onClick={() => onDelete(schedule)}
                className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
              >
                删除
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
