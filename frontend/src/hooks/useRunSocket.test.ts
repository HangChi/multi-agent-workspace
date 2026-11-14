/** useRunSocket 单元测试：心跳保活与终态关闭。
 *
 * 背景（此前的问题）：
 * - nginx proxy_read_timeout 300s，客户端不发 ping 时长任务日志连接会被静默掐断
 * - run 结束后连接空挂、且断线还会无意义重连
 */

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRunSocket } from './useRunSocket'
import { useAuthStore } from '../stores/authStore'

const HEARTBEAT_MS = 25_000

class MockWebSocket {
  static OPEN = 1
  static CLOSED = 3
  static instances: MockWebSocket[] = []

  url: string
  readyState = 0
  sent: string[] = []
  closed = false

  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    if (this.closed) return
    this.closed = true
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }

  // 测试辅助
  open() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.()
  }

  emit(event: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(event) })
  }
}

beforeEach(() => {
  MockWebSocket.instances = []
  vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket)
  useAuthStore.setState({ accessToken: 'test-token', refreshToken: 'r', user: null })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function setup(runId = 'run-1') {
  const onEvent = vi.fn()
  const view = renderHook(() => useRunSocket(runId, onEvent, true))
  return { onEvent, ...view }
}

describe('useRunSocket 心跳', () => {
  it('连接建立后每 25s 发送 ping', () => {
    vi.useFakeTimers()
    setup()
    const ws = MockWebSocket.instances[0]
    act(() => ws.open())

    expect(ws.sent).toHaveLength(0)
    vi.advanceTimersByTime(HEARTBEAT_MS)
    expect(ws.sent).toEqual([JSON.stringify({ type: 'ping' })])

    vi.advanceTimersByTime(HEARTBEAT_MS)
    expect(ws.sent).toHaveLength(2)
  })

  it('未连接时不发心跳', () => {
    vi.useFakeTimers()
    setup()
    vi.advanceTimersByTime(HEARTBEAT_MS * 2)
    expect(MockWebSocket.instances[0].sent).toHaveLength(0)
  })

  it('卸载后停止心跳（不泄漏定时器）', () => {
    vi.useFakeTimers()
    const { unmount } = setup()
    const ws = MockWebSocket.instances[0]
    act(() => ws.open())

    unmount()
    vi.advanceTimersByTime(HEARTBEAT_MS * 3)
    expect(ws.sent).toHaveLength(0)
  })
})

describe('useRunSocket 终态关闭', () => {
  it('收到 run_completed 后主动关闭且不再重连', () => {
    vi.useFakeTimers()
    setup()
    const ws = MockWebSocket.instances[0]
    act(() => ws.open())

    act(() => ws.emit({ type: 'run_completed', result: { answer: 'ok' } }))
    expect(ws.closed).toBe(true)

    // 终态后即便时间流逝也不应新建连接
    vi.advanceTimersByTime(60_000)
    expect(MockWebSocket.instances).toHaveLength(1)
  })

  it.each(['run_failed', 'run_cancelled'])('收到 %s 同样关闭', (type: string) => {
    vi.useFakeTimers()
    setup()
    const ws = MockWebSocket.instances[0]
    act(() => ws.open())

    act(() => ws.emit({ type }))
    expect(ws.closed).toBe(true)
    vi.advanceTimersByTime(60_000)
    expect(MockWebSocket.instances).toHaveLength(1)
  })

  it('非终态事件不关闭连接', () => {
    vi.useFakeTimers()
    setup()
    const ws = MockWebSocket.instances[0]
    act(() => ws.open())

    act(() => ws.emit({ type: 'step_new', step: { seq: 1 } }))
    expect(ws.closed).toBe(false)
  })
})

describe('useRunSocket 沙箱与出网事件', () => {
  it.each([
    { type: 'sandbox_started', execution_id: 'exec-1', language: 'python' },
    { type: 'sandbox_output', execution_id: 'exec-1', stream: 'stdout', chunk: 'ready' },
    { type: 'sandbox_finished', execution_id: 'exec-1', status: 'ok', exit_code: 0 },
    { type: 'egress_denied', execution_id: 'exec-1', target_ip: '169.254.169.254', port: 80 },
  ])('透传 $type 事件', (event) => {
    const { onEvent } = setup()
    const ws = MockWebSocket.instances[0]
    act(() => ws.open())
    act(() => ws.emit(event))
    expect(onEvent).toHaveBeenCalledWith(event)
    expect(ws.closed).toBe(false)
  })
})

describe('useRunSocket 重连', () => {
  it('异常断开后按退避重连', () => {
    vi.useFakeTimers()
    setup()
    const ws = MockWebSocket.instances[0]
    act(() => ws.open())

    act(() => ws.close()) // 非终态断开
    expect(MockWebSocket.instances).toHaveLength(1)

    vi.advanceTimersByTime(1000) // BASE_DELAY_MS
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  it('未登录（无 token）不建立连接', () => {
    useAuthStore.setState({ accessToken: null, refreshToken: null })
    setup()
    expect(MockWebSocket.instances).toHaveLength(0)
  })
})

describe('useRunSocket 事件回调', () => {
  it('解析后透传给回调，非法 JSON 静默忽略', () => {
    const { onEvent } = setup()
    const ws = MockWebSocket.instances[0]
    act(() => ws.open())

    act(() => {
      ws.onmessage?.({ data: '{"type":"cost_update","cost_cents":7}' })
      ws.onmessage?.({ data: 'not-json' })
    })

    expect(onEvent).toHaveBeenCalledWith({ type: 'cost_update', cost_cents: 7 })
    expect(onEvent).toHaveBeenCalledTimes(1) // 非法 JSON 不触发回调
  })
})
