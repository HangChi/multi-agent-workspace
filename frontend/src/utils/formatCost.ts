/** 成本展示：优先用微分（micro-cent）还原更低位的金额。
 *
 * 背景：后端 `cost_cents` 是四舍五入到整分的值，一次 gpt-4o-mini 调用约 0.015 分，
 * 按分展示会显示成 ¥0.00，看起来像"没花钱"。后端已改为按微分精确累计，
 * 这里在有微分时用 3 位小数展示小于 1 分钱的成本。
 */

/** 1 元 = 100 分 = 1e8 微分 */
const MICROS_PER_YUAN = 100_000_000

export function formatCostYuan(cents: number, micros?: number | null): string {
  if (typeof micros === 'number' && Number.isFinite(micros) && micros > 0) {
    const yuan = micros / MICROS_PER_YUAN
    // 小于 1 分钱时给 3 位小数，否则维持 2 位（避免 ¥12.340 这种噪音）
    return yuan < 0.01 ? yuan.toFixed(3) : yuan.toFixed(2)
  }
  return (cents / 100).toFixed(2)
}
