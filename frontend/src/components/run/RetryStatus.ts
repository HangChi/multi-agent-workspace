/**
 * 重试状态呈现（006 / T028）。
 *
 * 三条规则写在这里而不是散在页面里：
 *
 * 1. **retrying 与 failed 必须可辨**。用户按「失败」筛到一个其实 4 秒后会
 *    自己成功的任务，会去手动重试——而手动重试与自动重试会撞锁，白费一次操作
 *    （spec US4 场景 2）。
 * 2. **倒计时到点归零，不变负**。到点后重试可能还没开始（锁竞争、Worker 忙），
 *    显示「-3s」只会让人以为系统坏了。
 * 3. **未知状态降级显示原始字符串**。后端加新状态时前端不该崩，也不该显示空白
 *    （与 contracts §7 的约定一致）。
 */

import type { BadgeTone } from '../ui/Badge'
import type { RunStatus } from '../../api/runs'

interface StatusMeta {
  label: string
  tone: BadgeTone
}

const BASE: Record<RunStatus, StatusMeta> = {
  queued: { label: '排队中', tone: 'slate' },
  running: { label: '执行中', tone: 'sky' },
  needs_input: { label: '等待输入', tone: 'amber' },
  // 006：瞬时失败后的等待态。琥珀色，与 failed 的玫红明确区分。
  retrying: { label: '重试中', tone: 'amber' },
  completed: { label: '已完成', tone: 'emerald' },
  failed: { label: '失败', tone: 'rose' },
  cancelled: { label: '已取消', tone: 'slate' },
  timeout: { label: '超时', tone: 'orange' },
}

/**
 * 状态元数据。用 Proxy 兜住未知状态：直接 `Record` 索引会得到 undefined，
 * 页面再取 `.label` 就崩了；而「后端加了状态、前端还没跟上」是必然会发生的。
 */
export const RETRY_STATUS_META = new Proxy(BASE, {
  get(target, key: string) {
    if (key in target) return target[key as RunStatus]
    return { label: key, tone: 'slate' as BadgeTone }
  },
}) as Record<RunStatus, StatusMeta> & Record<string, StatusMeta>

/**
 * 距下次重试还有多少秒。
 *
 * @param nextRetryAt ISO 时间串；null 表示不在重试等待中
 * @returns 剩余秒数（向下取整，最小 0）；不在重试等待中返回 null
 */
export function formatRetryCountdown(nextRetryAt: string | null): number | null {
  if (!nextRetryAt) return null
  const target = new Date(nextRetryAt).getTime()
  if (Number.isNaN(target)) return null
  const remaining = Math.ceil((target - Date.now()) / 1000)
  return remaining > 0 ? remaining : 0
}

interface ProgressArgs {
  attempt: number
  maxAttempts: number | null
  secondsLeft: number | null
}

/** 「第 2/3 次 · 约 4s 后重试」——把三个数字拼成用户能直接读的一句话。 */
export function retryProgressText({ attempt, maxAttempts, secondsLeft }: ProgressArgs): string {
  const count = maxAttempts && maxAttempts > 1 ? `${attempt}/${maxAttempts}` : `${attempt}`
  const prefix = `第 ${count} 次`
  return secondsLeft === null ? `${prefix}尝试` : `${prefix} · 约 ${secondsLeft}s 后重试`
}
