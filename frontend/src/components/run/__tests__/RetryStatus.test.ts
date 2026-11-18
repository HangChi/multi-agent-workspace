import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

import { RETRY_STATUS_META, formatRetryCountdown, retryProgressText } from '../RetryStatus'

describe('RetryStatus（006）', () => {
  describe('RETRY_STATUS_META', () => {
    it('marks retrying with an amber tone distinct from failed rose', () => {
      // 与 failed 必须可辨：用户按「失败」筛到一个其实 4 秒后会自己成功的任务，
      // 会去手动重试，而手动重试与自动重试会撞锁（白费一次操作）。
      expect(RETRY_STATUS_META.retrying.tone).not.toBe(RETRY_STATUS_META.failed.tone)
      expect(RETRY_STATUS_META.retrying.label).toBe('重试中')
      expect(RETRY_STATUS_META.retrying.tone).toBe('amber')
    })

    it('degrades unknown status to its raw string', () => {
      // 后端加新状态时前端不能崩，也不能显示空白
      const meta = RETRY_STATUS_META['some_future_status' as keyof typeof RETRY_STATUS_META]
      expect(meta.label).toBe('some_future_status')
    })
  })

  describe('formatRetryCountdown', () => {
    beforeEach(() => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-11-18T09:00:00Z'))
    })
    afterEach(() => {
      vi.useRealTimers()
    })

    it('counts down from next_retry_at to zero', () => {
      expect(formatRetryCountdown('2026-11-18T09:00:04Z')).toBe(4)
      vi.advanceTimersByTime(2000)
      expect(formatRetryCountdown('2026-11-18T09:00:04Z')).toBe(2)
    })

    it('returns zero instead of a negative number once overdue', () => {
      // 到点后重试可能还没开始（锁竞争、Worker 忙），此时显示负数毫无意义
      expect(formatRetryCountdown('2026-11-18T08:59:59Z')).toBe(0)
      vi.advanceTimersByTime(60_000)
      expect(formatRetryCountdown('2026-11-18T08:59:59Z')).toBe(0)
    })

    it('returns null when there is no scheduled retry', () => {
      expect(formatRetryCountdown(null)).toBeNull()
    })
  })

  describe('retryProgressText', () => {
    it('shows attempt out of max with countdown', () => {
      expect(retryProgressText({ attempt: 2, maxAttempts: 3, secondsLeft: 4 })).toBe(
        '第 2/3 次 · 约 4s 后重试'
      )
    })

    it('omits the countdown while the retry is starting', () => {
      expect(retryProgressText({ attempt: 2, maxAttempts: 3, secondsLeft: null })).toBe(
        '第 2/3 次尝试'
      )
    })

    it('omits max when the server did not report it', () => {
      // 有倒计时时不需要「尝试」后缀——「约 4s 后重试」已经把话说完了
      expect(retryProgressText({ attempt: 2, maxAttempts: null, secondsLeft: 4 })).toBe(
        '第 2 次 · 约 4s 后重试'
      )
    })
  })
})
