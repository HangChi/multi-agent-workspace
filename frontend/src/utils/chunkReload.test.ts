/** chunk 失效识别与刷新节流测试。 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  clearReloadFlag,
  isChunkLoadError,
  readReloadFlag,
  shouldAttemptReload,
  writeReloadFlag,
} from './chunkReload'

describe('isChunkLoadError', () => {
  it.each([
    'Failed to fetch dynamically imported module: http://x/assets/BoardPage-abc.js',
    'error loading dynamically imported module',
    'Importing a module script failed.',
    'Expected a JavaScript module script but the server responded with a MIME type of "text/html"',
  ])('识别各浏览器措辞: %s', (message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true)
  })

  it('普通异常不误判', () => {
    expect(isChunkLoadError(new Error('渲染炸了'))).toBe(false)
    expect(isChunkLoadError(new TypeError('Cannot read properties of undefined'))).toBe(false)
  })

  it('非 Error 输入不误判', () => {
    expect(isChunkLoadError('Failed to fetch dynamically imported module')).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
    expect(isChunkLoadError(undefined)).toBe(false)
  })
})

describe('shouldAttemptReload', () => {
  it('没有历史标记时允许一次', () => {
    expect(shouldAttemptReload(0, 1_000_000)).toBe(true)
  })

  it('冷却期内不重复刷新（防死循环）', () => {
    const now = 1_000_000
    expect(shouldAttemptReload(now - 3_000, now)).toBe(false)
    expect(shouldAttemptReload(now - 9_999, now)).toBe(false)
  })

  it('超过冷却期允许再试', () => {
    const now = 1_000_000
    expect(shouldAttemptReload(now - 10_001, now)).toBe(true)
  })

  it('标记在未来（时钟回拨）也允许重试，不被永久卡住', () => {
    const now = 1_000_000
    expect(shouldAttemptReload(now + 60_000, now)).toBe(true)
  })
})

describe('刷新标记读写', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })
  afterEach(() => {
    window.sessionStorage.clear()
  })

  it('写入后可读回', () => {
    expect(readReloadFlag()).toBe(0)
    writeReloadFlag(123456)
    expect(readReloadFlag()).toBe(123456)
  })

  it('clear 后归零', () => {
    writeReloadFlag(999)
    clearReloadFlag()
    expect(readReloadFlag()).toBe(0)
  })

  it('存储里是脏数据时返回 0，不抛错', () => {
    window.sessionStorage.setItem('aw_chunk_reload_at', 'not-a-number')
    expect(readReloadFlag()).toBe(0)
  })
})
