/** extractError 单元测试：后端错误结构提取。 */

import { describe, expect, it } from 'vitest'

import { extractError } from './client'

describe('extractError', () => {
  it('提取后端结构化错误 message', () => {
    const err = {
      response: {
        data: { error: { code: 'RATE_LIMITED', message: '月配额已用尽' } },
      },
    }
    expect(extractError(err)).toBe('月配额已用尽')
  })

  it('无 response 时返回兜底文案', () => {
    expect(extractError(new Error('network'))).toBe('请求失败，请稍后重试')
  })

  it('response 无 error.message 时返回兜底文案', () => {
    const err = { response: { data: {} } }
    expect(extractError(err)).toBe('请求失败，请稍后重试')
  })

  it('处理 undefined/null', () => {
    expect(extractError(undefined)).toBe('请求失败，请稍后重试')
    expect(extractError(null)).toBe('请求失败，请稍后重试')
  })
})
