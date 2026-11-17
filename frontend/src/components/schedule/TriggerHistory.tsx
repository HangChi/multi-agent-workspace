/**
 * 触发历史抽屉（T052，US3/US4）。
 *
 * 四种结果的文案必须彼此可辨：`missed`（服务没跑）与 `skipped_busy`
 * （上次没跑完）在处置上完全相反——前者不用管，后者说明任务超时或系统过载。
 * 若两者都显示成"未执行"，这个抽屉就只是在堆日志。
 *
 * `missed` 记录额外展示「合并次数 + 窗口区间」：一次停机可能只留一条记录，
 * 但背后是 25 个被跳过的计划时刻，只显示一条会让人严重低估影响。
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { fetchScheduleTriggers } from '../../api/endpoints'
import type { ScheduleTriggerResult, TriggerCounts } from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import type { BadgeTone } from '../ui/Badge'
import { Icon } from '../ui/Icons'
import { Modal } from '../ui/Modal'
import { Pagination } from '../ui/Pagination'
import { SegmentedControl } from '../ui/Segmented'
import { Spinner } from '../ui/Spinner'

/**
 * 结果四态的展示元数据：文案与色调从这一个出口取。
 *
 * 四态必须各有中文名。只把 `skipped_busy` 原样打出来的话，
 * 用户看到的是一个他无法理解也不会去查的英文枚举。
 */
const RESULT_META: Record<ScheduleTriggerResult, { label: string; tone: BadgeTone }> = {
  fired: { label: '已触发', tone: 'emerald' },
  missed: { label: '已错过', tone: 'amber' },
  skipped_busy: { label: '已跳过', tone: 'sky' },
  failed: { label: '执行失败', tone: 'rose' },
}

const FILTERS: { value: ScheduleTriggerResult | 'all'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'fired', label: '已触发' },
  { value: 'missed', label: '已错过' },
  { value: 'skipped_busy', label: '已跳过' },
  { value: 'failed', label: '执行失败' },
]

const PAGE_SIZE = 10

interface Props {
  open: boolean
  onClose: () => void
  scheduleId: string
  scheduleName: string
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function TriggerHistory({ open, onClose, scheduleId, scheduleName }: Props) {
  const [result, setResult] = useState<ScheduleTriggerResult | 'all'>('all')
  const [page, setPage] = useState(1)

  const { data, isLoading } = useQuery({
    queryKey: ['schedules', scheduleId, 'triggers', result, page],
    queryFn: () =>
      fetchScheduleTriggers(scheduleId, {
        result: result === 'all' ? undefined : result,
        page,
        page_size: PAGE_SIZE,
      }),
    enabled: open,
  })

  const items = data?.items ?? []
  const counts: TriggerCounts = data?.counts ?? {
    fired: 0,
    missed: 0,
    skipped_busy: 0,
    failed: 0,
  }
  const totalAll = counts.fired + counts.missed + counts.skipped_busy + counts.failed

  const changeFilter = (next: ScheduleTriggerResult | 'all') => {
    setResult(next)
    // 换筛选要回第一页：否则可能停在新结果集不存在的页码上，看到空列表
    setPage(1)
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="xl"
      title={`触发历史 · ${scheduleName}`}
      description={
        totalAll > 0 ? (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 pt-1 text-xs">
            <span className="text-ink-muted">
              共 <span className="font-semibold tabular-nums text-ink">{totalAll}</span> 次
            </span>
            <span className="text-ink-faint">·</span>
            <span className="text-emerald-600">{counts.fired} 成功</span>
            <span className="text-amber-600">{counts.missed} 错过</span>
            <span className="text-sky-600">{counts.skipped_busy} 跳过</span>
            <span className="text-rose-600">{counts.failed} 失败</span>
          </span>
        ) : undefined
      }
      icon={
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-accent ring-1 ring-inset ring-brand-100">
          <Icon name="clock" className="h-[18px] w-[18px]" />
        </span>
      }
    >
      <div className="space-y-3">
        <SegmentedControl
          options={FILTERS}
          value={result}
          onChange={changeFilter}
          ariaLabel="按触发结果筛选"
        />

        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-12 text-sm text-ink-muted">
            <Spinner className="h-4 w-4 text-brand-500" />
            加载中…
          </div>
        ) : items.length === 0 ? (
          <div className="rounded-lg border border-line-subtle bg-surface-muted/60 px-4 py-10 text-center">
            <Icon name="clock" className="mx-auto mb-2 h-6 w-6 text-ink-faint" />
            <p className="text-sm font-medium text-ink-soft">
              {result === 'all' ? '还没有触发记录' : '没有该结果的记录'}
            </p>
            <p className="mt-1 text-xs text-ink-muted">
              {result === 'all'
                ? '规则启用后，每次到点都会在这里留下一条记录。'
                : '切换筛选查看其他结果。'}
            </p>
          </div>
        ) : (
          <ul className="space-y-2">
            {items.map((trigger) => {
              const meta = RESULT_META[trigger.result]
              return (
                <li
                  key={trigger.id}
                  className="rounded-lg border border-line/80 bg-surface p-3 shadow-card"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={meta.tone} dot>
                      {meta.label}
                    </Badge>
                    <span className="text-xs tabular-nums text-ink-muted">
                      计划 {formatTime(trigger.planned_at)}
                    </span>
                    {trigger.fired_at && (
                      <span className="text-2xs tabular-nums text-ink-faint">
                        实际 {formatTime(trigger.fired_at)}
                      </span>
                    )}
                    <span className="ml-auto flex items-center gap-2">
                      {trigger.request_id && (
                        <Link
                          to={`/?request=${trigger.request_id}`}
                          className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 text-2xs font-medium text-accent transition-colors hover:bg-brand-100"
                        >
                          <Icon name="external-link" className="h-2.5 w-2.5" />
                          查看需求
                        </Link>
                      )}
                      {trigger.run_id && (
                        <Link
                          to={`/runs/${trigger.run_id}`}
                          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-2xs font-medium text-ink-muted transition-colors hover:bg-surface-subtle hover:text-ink-soft"
                        >
                          <Icon name="play" className="h-2.5 w-2.5" />
                          运行
                        </Link>
                      )}
                    </span>
                  </div>

                  {/* 合并次数与窗口：一条记录背后可能是一整段停机 */}
                  {trigger.result === 'missed' && trigger.missed_count > 0 && (
                    <p className="mt-2 flex flex-wrap items-center gap-1.5 rounded-md bg-amber-50 px-2 py-1.5 text-xs leading-relaxed text-amber-800">
                      <Icon name="info" className="h-3.5 w-3.5 shrink-0" />
                      <span>
                        合并{' '}
                        <span className="font-semibold tabular-nums">{trigger.missed_count}</span>{' '}
                        次
                      </span>
                      {trigger.window_start && trigger.window_end && (
                        <span className="tabular-nums text-amber-700/90">
                          （{formatTime(trigger.window_start)} → {formatTime(trigger.window_end)}）
                        </span>
                      )}
                    </p>
                  )}

                  {trigger.reason && (
                    <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
                      {trigger.reason}
                    </p>
                  )}
                </li>
              )
            })}
          </ul>
        )}

        {(data?.total ?? 0) > PAGE_SIZE && (
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={data?.total ?? 0}
            onChange={setPage}
            loading={isLoading}
          />
        )}
      </div>
    </Modal>
  )
}
