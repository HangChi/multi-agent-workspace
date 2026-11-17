import { describe, expect, it } from 'vitest'

import { formatCostYuan } from './formatCost'

describe('formatCostYuan', () => {
  it('没有微分时按整分展示（保持旧口径）', () => {
    expect(formatCostYuan(1234)).toBe('12.34')
  })

  it('微分可还原到分以下：0.075 分显示为 0.001 元而不是 0.00', () => {
    // 0.075 分 = 0.00075 元
    expect(formatCostYuan(0, 75_000)).toBe('0.001')
  })

  it('金额较大时回到两位小数', () => {
    expect(formatCostYuan(0, 250_000_000)).toBe('2.50')
  })

  it('微分缺失或非正数时退回整分口径', () => {
    expect(formatCostYuan(500, null)).toBe('5.00')
    expect(formatCostYuan(500, 0)).toBe('5.00')
    expect(formatCostYuan(500, Number.NaN)).toBe('5.00')
  })
})
