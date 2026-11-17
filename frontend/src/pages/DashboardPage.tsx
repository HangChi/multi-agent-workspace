/** 仪表盘页（T078，Phase 8）：工作负载总览 + 近 7 日成本趋势。 */

import { useQuery } from '@tanstack/react-query'

import { fetchStatsOverview } from '../api/endpoints'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, CardHeader, IconTile } from '../components/ui/Card'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Icon } from '../components/ui/Icons'
import type { IconName } from '../components/ui/Icons'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { Skeleton } from '../components/ui/Skeleton'
import { cn } from '../components/ui/cn'

/** 本月成本配额（分）：与后端配置保持一致 */
const MONTHLY_QUOTA_CENTS = 1_000_000

function formatCents(cents: number): string {
  return `¥${(cents / 100).toFixed(2)}`
}

type Tone = 'brand' | 'sky' | 'emerald' | 'rose' | 'amber'

const TONE_TEXT: Record<Tone, string> = {
  brand: 'text-accent',
  sky: 'text-sky-700 dark:text-sky-300',
  emerald: 'text-emerald-700 dark:text-emerald-300',
  rose: 'text-rose-600 dark:text-rose-300',
  amber: 'text-amber-700 dark:text-amber-300',
}

function StatCard({
  label,
  value,
  sub,
  icon,
  tone = 'brand',
  badge,
}: {
  label: string
  value: string
  sub?: string
  icon: IconName
  tone?: Tone
  badge?: React.ReactNode
}) {
  return (
    <Card className="relative overflow-hidden">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs font-medium text-ink-muted">{label}</p>
          <p
            className={cn(
              'mt-1.5 text-2xl font-semibold tabular-nums tracking-tight',
              TONE_TEXT[tone]
            )}
          >
            {value}
          </p>
        </div>
        <IconTile tone={tone} size="md">
          <Icon name={icon} className="h-4 w-4" />
        </IconTile>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        {badge}
        {sub && <p className="text-xs leading-relaxed text-ink-faint">{sub}</p>}
      </div>
    </Card>
  )
}

/** 近 7 日成本趋势：柱状图 + 网格线 + hover 明细，不引入图表库。 */
function CostTrend({ data }: { data: number[] }) {
  const max = Math.max(...data, 1)
  const total = data.reduce((a, b) => a + b, 0)
  const labels = data.map((_, i) => {
    const d = new Date()
    d.setDate(d.getDate() - (6 - i))
    return `${d.getMonth() + 1}/${d.getDate()}`
  })

  return (
    <Card className="md:col-span-2">
      <CardHeader
        title="近 7 日成本趋势"
        description={`合计 ${formatCents(total)} · 日均 ${formatCents(Math.round(total / Math.max(data.length, 1)))}`}
        icon={
          <IconTile tone="brand" size="sm">
            <Icon name="activity" className="h-3.5 w-3.5" />
          </IconTile>
        }
        actions={<Badge tone="slate">单位：元</Badge>}
      />

      <div className="relative mt-5 h-40">
        {/* 网格线 */}
        <div
          aria-hidden="true"
          className="absolute inset-x-0 top-0 bottom-6 flex flex-col justify-between"
        >
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="border-t border-dashed border-line-subtle" />
          ))}
        </div>

        <div className="relative flex h-full items-end gap-1.5 sm:gap-2.5">
          {data.map((v, i) => {
            const pct = Math.max((v / max) * 100, v > 0 ? 8 : 3)
            return (
              <div key={i} className="group flex h-full flex-1 flex-col items-center justify-end">
                <div className="relative flex w-full flex-1 items-end">
                  <div
                    className={cn(
                      'w-full rounded-t-md transition-all duration-200',
                      v > 0
                        ? 'bg-gradient-to-t from-brand-500 to-brand-400 group-hover:from-brand-600 group-hover:to-brand-500'
                        : 'bg-line'
                    )}
                    style={{ height: `${pct}%` }}
                  />
                  {/* hover 明细 */}
                  <span className="pointer-events-none absolute -top-7 left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-md bg-slate-900 px-1.5 py-0.5 text-2xs font-medium text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
                    {formatCents(v)}
                  </span>
                </div>
                <span className="mt-1.5 text-2xs tabular-nums text-ink-faint">{labels[i]}</span>
              </div>
            )
          })}
        </div>
      </div>
    </Card>
  )
}

/** 配额占用环：比一条几乎看不到的细进度条更能表达「占比」这件事。 */
function QuotaRing({ pct }: { pct: number }) {
  const size = 76
  const stroke = 7
  const r = (size - stroke) / 2
  const circumference = 2 * Math.PI * r
  // 最小留 0.8% 的弧长，避免「有数据却完全空白」
  const filled = (Math.min(Math.max(pct, 0.8), 100) / 100) * circumference
  const color = pct >= 85 ? '#f43f5e' : pct >= 60 ? '#f59e0b' : '#10b981'

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full -rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          className="text-line-strong"
          stroke="currentColor"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference}`}
          className="transition-[stroke-dasharray] duration-500"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-sm font-semibold tabular-nums text-ink-soft">
          {pct < 1 ? pct.toFixed(1) : pct.toFixed(0)}%
        </span>
      </div>
    </div>
  )
}

/** 今日成本卡：大数字 + 配额环 + 环比，纵向撑满与右侧趋势卡对齐。 */
function CostCard({ cents, trend }: { cents: number; trend: number[] }) {
  const pct = (cents / MONTHLY_QUOTA_CENTS) * 100
  const remaining = Math.max(MONTHLY_QUOTA_CENTS - cents, 0)
  const yesterday = trend.length > 1 ? trend[trend.length - 2] : 0
  const delta = yesterday > 0 ? Math.round(((cents - yesterday) / yesterday) * 100) : null
  const danger = pct >= 80

  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        title="今日成本"
        description="本月配额 ¥10,000.00"
        icon={
          <IconTile tone="amber" size="sm">
            <Icon name="coins" className="h-3.5 w-3.5" />
          </IconTile>
        }
      />

      <div className="mt-4 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-3xl font-semibold tabular-nums tracking-tight text-ink">
            {formatCents(cents)}
          </p>
          <p className="mt-1.5 flex items-center gap-1 text-xs">
            {delta === null ? (
              <span className="text-ink-faint">暂无昨日数据</span>
            ) : (
              <span
                className={cn(
                  'inline-flex items-center gap-0.5 font-medium',
                  delta > 0 ? 'text-rose-600' : 'text-emerald-600'
                )}
              >
                <Icon name={delta > 0 ? 'arrow-up' : 'arrow-down'} className="h-3 w-3" />
                {Math.abs(delta)}%<span className="font-normal text-ink-faint">较昨日</span>
              </span>
            )}
          </p>
        </div>
        <QuotaRing pct={pct} />
      </div>

      <div className="mt-auto flex items-center justify-between border-t border-line-subtle pt-3 text-2xs text-ink-faint">
        <span>剩余配额 {formatCents(remaining)}</span>
        <span className="tabular-nums">已用 {pct.toFixed(2)}%</span>
      </div>

      {danger && (
        <p className="mt-2 flex items-center gap-1 text-2xs font-medium text-rose-600">
          <Icon name="alert-triangle" className="h-3 w-3" />
          配额即将用尽，请联系管理员调整
        </p>
      )}
    </Card>
  )
}

export function DashboardPage() {
  const { data, isLoading, error, refetch, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ['stats', 'overview'],
    queryFn: fetchStatsOverview,
    refetchInterval: 15_000, // 15s 轮询，成本/状态实时性
  })

  const header = (
    <PageHeader
      title="仪表盘"
      description="我的工作负载与成本概览，每 15 秒自动刷新。"
      icon={
        <IconTile tone="brand" size="lg">
          <Icon name="activity" className="h-5 w-5" />
        </IconTile>
      }
      actions={
        <>
          {dataUpdatedAt > 0 && (
            <span className="hidden text-xs text-ink-faint sm:block">
              更新于 {new Date(dataUpdatedAt).toLocaleTimeString('zh-CN')}
            </span>
          )}
          <Button
            variant="secondary"
            leftIcon="refresh"
            loading={isFetching && !isLoading}
            onClick={() => void refetch()}
          >
            刷新
          </Button>
        </>
      }
    />
  )

  if (isLoading) {
    return (
      <Page>
        {header}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Card key={i}>
              <Skeleton className="h-3 w-16" />
              <Skeleton className="mt-3 h-7 w-20" />
              <Skeleton className="mt-3 h-3 w-24" />
            </Card>
          ))}
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <Card>
            <Skeleton className="h-3 w-16" />
            <Skeleton className="mt-3 h-9 w-28" />
            <Skeleton className="mt-4 h-1.5 w-full" />
          </Card>
          <Card className="md:col-span-2">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="mt-5 h-40 w-full" />
          </Card>
        </div>
      </Page>
    )
  }

  if (error || !data) {
    return (
      <Page>
        {header}
        <ErrorBanner
          error={error ?? '统计加载失败'}
          retry={() => void refetch()}
          className="mt-0"
        />
      </Page>
    )
  }

  const ratePct = (data.success_rate_7d * 100).toFixed(1)
  const rateHealthy = data.success_rate_7d >= 0.8

  return (
    <Page>
      {header}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          label="执行中"
          value={String(data.running)}
          sub="Agent 正在处理的任务"
          icon="play"
          tone="sky"
          badge={
            data.running > 0 ? (
              <Badge tone="sky" dot pulse>
                运行中
              </Badge>
            ) : (
              <Badge tone="slate">空闲</Badge>
            )
          }
        />
        <StatCard
          label="排队中"
          value={String(data.queued)}
          sub="等待 Worker 领取"
          icon="clock"
          tone="brand"
        />
        <StatCard
          label="今日完成"
          value={String(data.completed_today)}
          sub={`今日失败 ${data.failed_today}`}
          icon="check-circle"
          tone="emerald"
        />
        <StatCard
          label="近 7 日成功率"
          value={`${ratePct}%`}
          sub="完成 /（完成 + 失败）"
          icon="target"
          tone={rateHealthy ? 'emerald' : 'rose'}
          badge={
            <Badge tone={rateHealthy ? 'emerald' : 'rose'} dot>
              {rateHealthy ? '健康' : '需关注'}
            </Badge>
          }
        />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <CostCard cents={data.cost_today_cents} trend={data.cost_trend_7d} />
        <CostTrend data={data.cost_trend_7d} />
      </div>
    </Page>
  )
}
