import { apiClient } from './client'
import type { RoutingStrategy } from './groups'
import type { RequestPriority } from './requests'

// ---------- 定时调度（004，T050）----------

export type ScheduleTargetType = 'agent' | 'group'
export type ScheduleTriggerResult = 'fired' | 'missed' | 'skipped_busy' | 'failed'
export type TargetStatus = 'active' | 'archived' | 'missing'

export interface ScheduleTemplate {
  title: string
  description: string | null
  expected_output: string | null
  priority: RequestPriority
}

export interface ScheduleTarget {
  type: ScheduleTargetType
  id: string
  strategy: RoutingStrategy | null
}

/** 输出用目标：多两个展示字段（name 在目标被物理删除时为 null）。 */
export interface ScheduleTargetOut extends ScheduleTarget {
  name: string | null
  status: TargetStatus
}

export interface Schedule {
  id: string
  owner_id: string
  /** 仅 admin 视角填充；member 看自己时为 null。 */
  owner_email: string | null
  name: string
  description: string | null
  cron_expr: string
  timezone: string
  template: ScheduleTemplate
  target: ScheduleTargetOut
  enabled: boolean
  next_run_at: string | null
  last_run_at: string | null
  last_result: ScheduleTriggerResult | null
  consecutive_failures: number
  auto_disabled_reason: string | null
  created_at: string
  updated_at: string
}

export interface ScheduleCreateRequest {
  name: string
  description?: string | null
  cron_expr: string
  timezone?: string
  template: ScheduleTemplate
  target: ScheduleTarget
  enabled?: boolean
}

/**
 * 部分更新。
 *
 * 字段缺席表示「不改」，所以这里**不能**写成 `Partial<ScheduleCreateRequest>`
 * 就完事：`description: null` 在后端语义里也是「不改」（清空要用空字符串），
 * 传 null 反而容易让人以为能清空。类型上仍允许 null 是因为 target/template
 * 两个嵌套对象确实以 null 表示不改。
 */
export interface ScheduleUpdateRequest {
  name?: string
  description?: string
  cron_expr?: string
  timezone?: string
  template?: Partial<ScheduleTemplate>
  target?: ScheduleTarget
  enabled?: boolean
}

export interface ScheduleList {
  items: Schedule[]
  total: number
}

export interface ScheduleTrigger {
  id: string
  schedule_id: string
  planned_at: string
  /** 错过记录没有实际执行时刻。 */
  fired_at: string | null
  result: ScheduleTriggerResult
  reason: string | null
  missed_count: number
  window_start: string | null
  window_end: string | null
  request_id: string | null
  run_id: string | null
}

export interface TriggerCounts {
  fired: number
  missed: number
  skipped_busy: number
  failed: number
}

export interface TriggerList {
  items: ScheduleTrigger[]
  total: number
  /** 全量分布（非当前页、非当前过滤）。 */
  counts: TriggerCounts
}

export interface CronPreviewRequest {
  cron_expr: string
  timezone?: string
  count?: number
}

export interface CronPreview {
  next_runs: string[]
  /** 解析失败时的可读原因；此时 next_runs 为空。 */
  error: string | null
}

/** 规则列表（member 仅自己，admin 全部；viewer 403）。 */
export async function fetchSchedules(params?: {
  enabled?: boolean
  page?: number
  page_size?: number
}): Promise<ScheduleList> {
  const { data } = await apiClient.get<ScheduleList>('/schedules', { params })
  return data
}

/** 规则详情。 */
export async function fetchSchedule(id: string): Promise<Schedule> {
  const { data } = await apiClient.get<Schedule>(`/schedules/${id}`)
  return data
}

/** 创建规则。 */
export async function createSchedule(payload: ScheduleCreateRequest): Promise<Schedule> {
  const { data } = await apiClient.post<Schedule>('/schedules', payload)
  return data
}

/** 编辑规则（只传要改的字段）。 */
export async function updateSchedule(
  id: string,
  payload: ScheduleUpdateRequest
): Promise<Schedule> {
  const { data } = await apiClient.patch<Schedule>(`/schedules/${id}`, payload)
  return data
}

/** 删除规则（既有需求保留，来源置空）。 */
export async function deleteSchedule(id: string): Promise<void> {
  await apiClient.delete(`/schedules/${id}`)
}

/** 预览接下来几次触发时间（200 + error 表达解析失败，不抛）。 */
export async function previewCron(payload: CronPreviewRequest): Promise<CronPreview> {
  const { data } = await apiClient.post<CronPreview>('/schedules/preview', payload)
  return data
}

/** 触发历史（分页 + 过滤 + 全量 counts）。 */
export async function fetchScheduleTriggers(
  scheduleId: string,
  params?: {
    result?: ScheduleTriggerResult
    page?: number
    page_size?: number
  }
): Promise<TriggerList> {
  const { data } = await apiClient.get<TriggerList>(`/schedules/${scheduleId}/triggers`, { params })
  return data
}
