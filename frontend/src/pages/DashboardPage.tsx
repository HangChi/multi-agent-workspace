/** 仪表盘页（T078，Phase 8）：工作负载总览 + 近 7 日成本趋势 + 今日健康度。
 *
 * 版式取舍：
 * - 指标数字用展示字族 + 32px 尺寸，靠「大数字」建立第一眼层级，
 *   而不是给每张卡都加彩色底（那会让四张卡抢同一份注意力）；
 * - 成本趋势用柱状而非折线：按天聚合的离散值，柱子比连线更诚实，
 *   且 7 根柱子的高度差一眼可比；
 * - 右列补一张「今日健康度」，把完成/失败与成功率放在一起解释，
 *   否则只有 4 个孤立数字，用户仍需自己算。
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

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

/**
 * 本月成本配额（分）。
 *
 * 与后端 settings.monthly_quota_cents 默认值一致（app/core/config.py，
 * 默认 1_000_000 = ¥10,000）。统计接口暂不下发配额，前端只能镜像这个
 * 常量——所有展示文案从它派生，不再有第二处写死的 ¥10,000.00。
 */
const MONTHLY_QUOTA_CENTS = 1_000_000

/** 轮询间隔（ms）：页面文案与 useQuery 的 refetchInterval 共用一份。 */
const REFRESH_MS = 15_000

function formatCents(cents: number): string {
  return `¥${(cents / 100).toFixed(2)}`
}

type Tone = 'brand' | 'sky' | 'emerald' | 'rose' | 'amber' | 'slate'

const TONE_TEXT: Record<Tone, string> = {
  brand: 'text-accent',
  sky: 'text-sky-600 dark:text-sky-300',
  emerald: 'text-emerald-600 dark:text-emerald-300',
  rose: 'text-rose-600 dark:text-rose-300',
  amber: 'text-amber-600 dark:text-amber-300',
  // 空数据中性档：不制造「出事了」的错觉
  slate: 'text-ink-soft',
}

/** 指标卡：数字最大，标签次之，解释最小。 */
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
    <Card className="relative overflow-hidden" padding="md">
      {/* 右上角一块极淡的品牌色晕染：白卡不再「素面朝天」，
          色相与图标底板呼应，整卡有了主题感 */}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full bg-gradient-to-br from-brand-100/70 to-transparent dark:from-brand-500/10"
      />
      <div className="relative flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-ui font-medium text-ink-muted">{label}</p>
          <p
            className={cn(
              'text-metric mt-1.5 font-display text-[32px] font-semibold leading-none',
              TONE_TEXT[tone]
            )}
          >
            {value}
          </p>
        </div>
        <IconTile tone={tone} size="lg">
          <Icon name={icon} className="h-[18px] w-[18px]" />
        </IconTile>
      </div>
      <div className="relative mt-3 flex flex-wrap items-center gap-x-2 gap-y-1">
        {badge}
        {sub && <p className="text-micro leading-relaxed text-ink-faint">{sub}</p>}
      </div>
    </Card>
  )
}

/** 近 7 日成本趋势：柱子 + 网格 + hover/点选明细，不引入图表库。 */
function CostTrend({ data }: { data: number[] }) {
  const max = Math.max(...data, 1)
  const total = data.reduce((a, b) => a + b, 0)
  const labels = data.map((_, i) => {
    const d = new Date()
    d.setDate(d.getDate() - (6 - i))
    return { day: `${d.getMonth() + 1}/${d.getDate()}`, weekday: '日一二三四五六'[d.getDay()] }
  })
  const todayIdx = data.length - 1
  // 点选（触摸）：hover 提示在触屏上不可见，点一下柱子钉住数值
  const [pinned, setPinned] = useState<number | null>(null)
  // 轴标签自适应精度：总额不足 1 元时固定两位小数，
  // 否则「¥0.01 / ¥0.01 / ¥0」三行重复数字像坏掉的轴
  const axisFmt = (v: number) =>
    total < 100 ? `¥${(v / 100).toFixed(2)}` : `¥${(v / 100).toFixed(1)}`
  // 中轴与顶轴相同（总额只有一两分）时不渲染中轴：重复数字比缺一个刻度更糟
  const midLabel = axisFmt(Math.round(max / 2))
  const showMid = midLabel !== axisFmt(max) && midLabel !== '¥0'

  return (
    <Card padding="lg" className="flex h-full flex-col">
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

      {/* 读屏通道：柱子是图形，配一张等价的隐藏表格，
          趋势信息不依赖视觉也能完整获取 */}
      <table className="sr-only">
        <caption>近 7 日每日成本（元）</caption>
        <tbody>
          {data.map((v, i) => (
            <tr key={i}>
              <th scope="row">{labels[i].day}</th>
              <td>{(v / 100).toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mt-6 flex flex-1 items-stretch gap-3">
        {/* 纵轴刻度：只标最大与 0，避免一排数字把图压碎 */}
        <div
          aria-hidden="true"
          className="flex w-14 shrink-0 flex-col justify-between pb-6 text-right text-micro tabular-nums text-ink-faint"
        >
          <span>{axisFmt(max)}</span>
          {showMid && <span>{midLabel}</span>}
          <span>¥0</span>
        </div>

        <div className="relative min-w-0 flex-1">
          <div
            aria-hidden="true"
            className="absolute inset-x-0 bottom-6 top-0 flex flex-col justify-between"
          >
            {[0, 1, 2].map((i) => (
              <div key={i} className="border-t border-dashed border-line-subtle" />
            ))}
          </div>

          <div
            aria-hidden="true"
            className="relative flex h-full min-h-[200px] items-end gap-1.5 sm:gap-2.5"
          >
            {data.map((v, i) => {
              const pct = Math.max((v / max) * 100, v > 0 ? 10 : 2)
              const isToday = i === todayIdx
              const showTip = pinned === i
              return (
                <div
                  key={i}
                  className="group flex h-full flex-1 flex-col items-center justify-end"
                  // 触摸设备没有 hover：点击柱子钉住/取消数值提示
                  onClick={() => setPinned(showTip ? null : i)}
                >
                  {/* 柱子限宽：只有一两天有数据时会变成几根巨柱，限宽后仍是「图」而不是「墙」 */}
                  <div className="relative flex h-full w-full max-w-[2.75rem] flex-1 items-end">
                    {/* 基线轨道：全高浅底，空数据的日子也是一根「看得见的柱子」，
                        图表不会因为没数据就散架 */}
                    <span className="absolute inset-x-0 bottom-0 top-0 rounded-t-[5px] bg-line/25" />
                    <div
                      className={cn(
                        'relative w-full rounded-t-[5px] transition-all duration-200',
                        v > 0
                          ? isToday
                            ? 'bg-gradient-to-t from-brand-600 to-brand-400 shadow-[0_0_12px_rgb(var(--brand-500)/0.35)]'
                            : 'bg-gradient-to-t from-brand-500/85 to-brand-400/75 group-hover:from-brand-500 group-hover:to-brand-400'
                          : 'bg-transparent'
                      )}
                      style={{ height: `${pct}%` }}
                    />
                    <span
                      className={cn(
                        'pointer-events-none absolute -top-7 left-1/2 z-10 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-surface px-1.5 py-0.5 text-micro font-medium text-ink shadow-pop transition-opacity',
                        showTip ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                      )}
                    >
                      {formatCents(v)}
                    </span>
                  </div>
                  <span
                    className={cn(
                      'mt-1.5 text-micro tabular-nums',
                      isToday ? 'font-semibold text-accent' : 'text-ink-faint'
                    )}
                  >
                    {isToday ? '今天' : labels[i].day}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </Card>
  )
}

/** 配额占用环：比一条几乎看不到的细进度条更能表达「占比」这件事。 */
function QuotaRing({ pct }: { pct: number }) {
  const size = 84
  const stroke = 8
  const r = (size - stroke) / 2
  const circumference = 2 * Math.PI * r
  // 最小留 0.8% 的弧长，避免「有数据却完全空白」
  const filled = (Math.min(Math.max(pct, 0.8), 100) / 100) * circumference
  // 档位色走 Tailwind 令牌类（stroke-*），不写死 hex——两套主题都成立
  const ringClass =
    pct >= 85 ? 'stroke-rose-500' : pct >= 60 ? 'stroke-amber-500' : 'stroke-brand-500'
  const ringId = 'quota-ring-gradient'

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full -rotate-90">
        {/* 渐变描边：实心色环像贴纸，同色系微渐变有「光影」 */}
        <defs>
          <linearGradient id={ringId} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="rgb(var(--brand-400))" />
            <stop offset="100%" stopColor="rgb(var(--brand-600))" />
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          className="stroke-line"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference}`}
          // 健康档用品牌渐变；告警档（≥60%）保持纯色，语义优先于装饰
          stroke={pct >= 60 ? undefined : `url(#${ringId})`}
          className={cn('transition-[stroke-dasharray] duration-500', ringClass)}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-metric text-sm font-semibold text-ink">
          {pct < 1 ? pct.toFixed(2) : pct.toFixed(1)}%
        </span>
        <span className="text-2xs text-ink-faint">已用</span>
      </div>
    </div>
  )
}

function CostCard({ cents, trend }: { cents: number; trend: number[] }) {
  const pct = (cents / MONTHLY_QUOTA_CENTS) * 100
  const remaining = Math.max(MONTHLY_QUOTA_CENTS - cents, 0)
  const yesterday = trend.length > 1 ? trend[trend.length - 2] : 0
  const delta = yesterday > 0 ? Math.round(((cents - yesterday) / yesterday) * 100) : null
  const danger = pct >= 80

  return (
    <Card className="flex flex-col" padding="lg">
      <CardHeader
        title="今日成本"
        description={`本月配额 ${formatCents(MONTHLY_QUOTA_CENTS)}`}
        icon={
          <IconTile tone="amber" size="sm">
            <Icon name="coins" className="h-3.5 w-3.5" />
          </IconTile>
        }
      />

      <div className="mt-5 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-metric font-display text-[32px] font-semibold leading-none text-ink">
            {formatCents(cents)}
          </p>
          <p className="mt-2 flex items-center gap-1 text-xs">
            {delta === null ? (
              <span className="text-ink-faint">暂无昨日数据</span>
            ) : (
              <span
                className={cn(
                  'inline-flex items-center gap-0.5 font-medium',
                  delta > 0
                    ? 'text-rose-600 dark:text-rose-300'
                    : 'text-emerald-600 dark:text-emerald-300'
                )}
              >
                <Icon name={delta > 0 ? 'arrow-up' : 'arrow-down'} className="h-3 w-3" />
                {Math.abs(delta)}%<span className="font-normal text-ink-faint"> 较昨日</span>
              </span>
            )}
          </p>
        </div>
        <QuotaRing pct={pct} />
      </div>

      <div className="mt-auto flex items-center justify-between border-t border-line-subtle pt-3 text-micro text-ink-faint">
        <span>剩余配额 {formatCents(remaining)}</span>
        <span className="tabular-nums">额度 ¥{MONTHLY_QUOTA_CENTS / 100}</span>
      </div>

      {danger && (
        <p className="mt-2 flex items-center gap-1 text-micro font-medium text-rose-600 dark:text-rose-300">
          <Icon name="alert-triangle" className="h-3 w-3" />
          配额即将用尽，请联系管理员调整
        </p>
      )}
    </Card>
  )
}

/** 今日健康度：完成 / 失败堆叠条 + 成功率结论。 */
function HealthCard({
  completed,
  failed,
  rate,
}: {
  completed: number
  failed: number
  rate: number
}) {
  const total = completed + failed
  const okPct = total > 0 ? (completed / total) * 100 : 100
  // 今天没有结束的运行时不算「不健康」——空系统显示红色告警是误报，
  // 视觉上也是一片刺眼的红，改中性的「暂无数据」
  const noData = total === 0
  const healthy = noData ? null : rate >= 0.8
  const healthTone = healthy === null ? 'slate' : healthy ? 'emerald' : 'rose'

  return (
    <Card className="flex flex-col" padding="lg">
      <CardHeader
        title="今日健康度"
        description="今天结束的运行里，成功占多少"
        icon={
          <IconTile tone={healthTone} size="sm">
            <Icon name="target" className="h-3.5 w-3.5" />
          </IconTile>
        }
        actions={
          <Badge tone={healthTone} dot>
            {healthy === null ? '暂无数据' : healthy ? '健康' : '需关注'}
          </Badge>
        }
      />

      <div className="mt-5 flex items-end justify-between gap-3">
        <div>
          <p className="text-metric font-display text-[32px] font-semibold leading-none text-ink">
            {noData ? '—' : `${Math.round(okPct)}%`}
          </p>
          <p className="mt-2 text-xs text-ink-faint">
            {noData ? '今天还没有结束的运行' : `成功 ${completed} · 失败 ${failed}`}
          </p>
        </div>
        <div className="text-right text-micro text-ink-faint">
          <p>近 7 日成功率</p>
          <p className="text-metric mt-0.5 text-base font-semibold text-ink-soft">
            {(rate * 100).toFixed(1)}%
          </p>
        </div>
      </div>

      {/* 完成/失败堆叠条：比例比两个数字更容易读 */}
      <div className="mt-4 flex h-2 overflow-hidden rounded-full bg-line">
        {noData ? (
          <span className="h-full w-full bg-line" />
        ) : (
          <>
            <span
              className="h-full bg-gradient-to-r from-emerald-400 to-emerald-500 transition-all duration-500"
              style={{ width: `${okPct}%` }}
            />
            <span className="h-full flex-1 bg-rose-400/80" />
          </>
        )}
      </div>
    </Card>
  )
}

export function DashboardPage() {
  const { data, isLoading, error, refetch, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ['stats', 'overview'],
    queryFn: fetchStatsOverview,
    refetchInterval: REFRESH_MS, // 15s 轮询，成本/状态实时性
  })

  const header = (
    <PageHeader
      eyebrow="工作台"
      title="仪表盘"
      description={`我的工作负载与成本概览，每 ${REFRESH_MS / 1000} 秒自动刷新。`}
      icon={<Icon name="activity" />}
      actions={
        <>
          {dataUpdatedAt > 0 && (
            <span className="text-xs text-ink-faint sm:block">
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
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Card key={i}>
              <Skeleton className="h-3 w-16" />
              <Skeleton className="mt-3 h-8 w-20" />
              <Skeleton className="mt-3 h-3 w-24" />
            </Card>
          ))}
        </div>
        <div className="grid gap-3 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <Skeleton className="h-3 w-28" />
            <Skeleton className="mt-5 h-44 w-full" />
          </Card>
          <Card>
            <Skeleton className="h-3 w-16" />
            <Skeleton className="mt-3 h-9 w-28" />
            <Skeleton className="mt-4 h-1.5 w-full" />
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
  // 空系统（近 7 日没有任何结束的运行）不算「不健康」：中性色 + 暂无数据，
  // 而不是一片刺眼的红——误报比不报更伤信任
  const hasRuns = data.completed_today + data.failed_today > 0 || data.success_rate_7d > 0
  const rateHealthy = hasRuns && data.success_rate_7d >= 0.8
  const rateTone = !hasRuns ? 'slate' : rateHealthy ? 'emerald' : 'rose'

  return (
    <Page>
      {header}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
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
          sub={hasRuns ? '完成 /（完成 + 失败）' : '近 7 日暂无结束的运行'}
          icon="target"
          tone={rateTone}
          badge={
            <Badge tone={rateTone} dot>
              {!hasRuns ? '暂无数据' : rateHealthy ? '健康' : '需关注'}
            </Badge>
          }
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <CostTrend data={data.cost_trend_7d} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
          <CostCard cents={data.cost_today_cents} trend={data.cost_trend_7d} />
          <HealthCard
            completed={data.completed_today}
            failed={data.failed_today}
            rate={data.success_rate_7d}
          />
        </div>
      </div>

      {/* 快捷入口：仪表盘只回答「现在怎么样」，下一步动作收在这里，
          免得用户看完数字还要回侧栏找入口 */}
      <section aria-labelledby="quick-actions">
        <h2 id="quick-actions" className="label-caps mb-2.5">
          下一步
        </h2>
        <div className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
          {[
            {
              to: '/',
              icon: 'grid' as IconName,
              label: '新建需求',
              hint: '写卡片并拖给 Agent',
              tone: 'brand' as const,
            },
            {
              to: '/agents',
              icon: 'bot' as IconName,
              label: '管理 Agent',
              hint: '提示词、模型与工具集',
              tone: 'sky' as const,
            },
            {
              to: '/groups',
              icon: 'users' as IconName,
              label: '编排 Agent 组',
              hint: '把多个 Agent 串成流水线',
              tone: 'amber' as const,
            },
            {
              to: '/settings/schedules',
              icon: 'clock' as IconName,
              label: '配置定时规则',
              hint: '按周期自动建单',
              tone: 'emerald' as const,
            },
          ].map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="group flex items-center gap-2.5 rounded-xl border border-line/80 bg-surface px-3 py-2.5 shadow-card transition-all duration-200 ease-out-expo hover:-translate-y-0.5 hover:border-line-strong hover:shadow-lift focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/40"
            >
              {/* 图标按目的地染色：四个入口一眼可分，不再是四块一样的灰 */}
              <IconTile tone={item.tone} size="sm">
                <Icon name={item.icon} className="h-3.5 w-3.5" />
              </IconTile>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-ui font-medium text-ink">{item.label}</span>
                <span className="block truncate text-micro text-ink-faint">{item.hint}</span>
              </span>
              <Icon
                name="arrow-right"
                className="h-3.5 w-3.5 shrink-0 -translate-x-1 text-ink-faint opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100"
              />
            </Link>
          ))}
        </div>
      </section>
    </Page>
  )
}
