/** 仪表盘页（T078，Phase 8）：工作负载总览 + 近 7 日成本趋势。 */

import { useQuery } from '@tanstack/react-query'

import { fetchStatsOverview } from '../api/endpoints'
import { ErrorBanner } from '../components/ui/ErrorBanner'

function formatCents(cents: number): string {
  return `¥${(cents / 100).toFixed(2)}`
}

function StatCard({
  label,
  value,
  sub,
  tone = 'slate',
}: {
  label: string
  value: string
  sub?: string
  tone?: 'slate' | 'blue' | 'green' | 'red'
}) {
  const tones: Record<string, string> = {
    slate: 'border-slate-200 bg-slate-50',
    blue: 'border-blue-200 bg-blue-50',
    green: 'border-emerald-200 bg-emerald-50',
    red: 'border-red-200 bg-red-50',
  }
  const valueTones: Record<string, string> = {
    slate: 'text-slate-800',
    blue: 'text-blue-700',
    green: 'text-emerald-700',
    red: 'text-red-600',
  }
  return (
    <div className={`rounded-xl border p-4 ${tones[tone]}`}>
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${valueTones[tone]}`}>{value}</p>
      {sub && <p className="mt-1 text-xs text-slate-500">{sub}</p>}
    </div>
  )
}

/** 近 7 日成本迷你柱状图（纯 div，无外部图表依赖）。 */
function CostTrend({ data }: { data: number[] }) {
  const max = Math.max(...data, 1)
  const labels = data.map((_, i) => {
    const d = new Date()
    d.setDate(d.getDate() - (6 - i))
    return `${d.getMonth() + 1}/${d.getDate()}`
  })
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <p className="text-sm font-medium text-slate-700">近 7 日成本趋势</p>
      <div className="mt-3 flex h-32 items-end gap-1.5">
        {data.map((v, i) => (
          <div key={i} className="flex flex-1 flex-col items-center gap-1">
            <span className="text-[10px] text-slate-500">
              {v > 0 ? `¥${(v / 100).toFixed(1)}` : ''}
            </span>
            <div
              className="w-full rounded-t bg-blue-500"
              style={{
                height: `${Math.max((v / max) * 100, v > 0 ? 6 : 2)}%`,
                opacity: v > 0 ? 1 : 0.25,
              }}
            />
            <span className="text-[10px] text-slate-400">{labels[i]}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function DashboardPage() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['stats', 'overview'],
    queryFn: fetchStatsOverview,
    refetchInterval: 15_000, // 15s 轮询，成本/状态实时性
  })

  if (isLoading) {
    return <div className="text-slate-500">加载中...</div>
  }
  if (error || !data) {
    return (
      <ErrorBanner
        error={error ?? '统计加载失败'}
        retry={() => refetch()}
      />
    )
  }

  const ratePct = (data.success_rate_7d * 100).toFixed(1)

  return (
    <div>
      <div className="mb-4">
        <h1 className="text-2xl font-bold text-slate-800">仪表盘</h1>
        <p className="mt-1 text-sm text-slate-500">我的工作负载与成本概览（每 15s 刷新）</p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard label="执行中" value={String(data.running)} tone="blue" />
        <StatCard label="排队中" value={String(data.queued)} />
        <StatCard
          label="今日完成"
          value={String(data.completed_today)}
          sub={`今日失败 ${data.failed_today}`}
          tone="green"
        />
        <StatCard
          label="近 7 日成功率"
          value={`${ratePct}%`}
          sub="完成 / (完成+失败)"
          tone={data.success_rate_7d >= 0.8 ? 'green' : 'red'}
        />
      </div>

      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
        <StatCard
          label="今日成本"
          value={formatCents(data.cost_today_cents)}
          sub="本月配额 ¥10000.00"
          tone="blue"
        />
        <CostTrend data={data.cost_trend_7d} />
      </div>
    </div>
  )
}
