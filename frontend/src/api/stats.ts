import { apiClient } from './client'

// ---------- 统计（T078，Phase 8）----------

export interface StatsOverview {
  running: number
  queued: number
  completed_today: number
  failed_today: number
  success_rate_7d: number
  cost_today_cents: number
  cost_trend_7d: number[]
  sandbox_runs_today: number
  sandbox_minutes_today: number
}

/** 仪表盘总览统计。 */
export async function fetchStatsOverview(): Promise<StatsOverview> {
  const { data } = await apiClient.get<StatsOverview>('/stats/overview')
  return data
}
