/**
 * 运行详情页：外部刷新不得清空已显示的日志（docs/code-audit-2026-11-21.md D3）。
 *
 * 背景：`load()` 同时被三处调用——首屏/切 run、WS 重连成功补数、WS 放弃后
 * 每 10 秒降级轮询。清空日志/回复草稿的代码原先写在 `load()` 里，于是
 * 用户已经看到的日志会在**每次重连、每 10 秒**被清一次（缓冲区仅 200 行且
 * 无回放，长会话里日志基本存不下来）。
 *
 * 本测试直接复现最直观的那条路径：WS 推一条日志 -> 触发降级轮询（socketStatus
 * 变 failed 后每 10s 调一次 load）-> 日志必须还在。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RunDetail } from '../../api/runs'
import { ConfirmProvider } from '../../components/ui/ConfirmDialog'
import { ToastContext, type ToastApi } from '../../components/ui/useToast'
import { useAuthStore } from '../../stores/authStore'
import { RunDetailPage } from '../RunDetailPage'

// --- WebSocket 桩：可控地推事件、可触发断线 ---
class MockWebSocket {
  static OPEN = 1
  static CLOSED = 3
  static instances: MockWebSocket[] = []

  url: string
  protocols: string[] | string | undefined
  readyState = 0
  sent: string[] = []
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string, protocols?: string[]) {
    this.url = url
    this.protocols = protocols
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

  open() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.()
  }

  emit(event: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(event) })
  }
}

vi.mock('../../api/endpoints', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/endpoints')>()
  return { ...actual, fetchRun: vi.fn() }
})

import { fetchRun } from '../../api/endpoints'

function makeRun(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    id: 'run-1',
    request_id: 'req-1',
    owner_id: 'owner-1',
    assignee_type: 'agent',
    assignee_id: 'agent-1',
    strategy: 'direct',
    status: 'running',
    current_step: 0,
    attempt: 1,
    next_retry_at: null,
    retry_history: [],
    result: null,
    error: null,
    cost_cents: 0,
    started_at: '2026-11-18T00:00:00Z',
    finished_at: null,
    created_at: '2026-11-18T00:00:00Z',
    updated_at: '2026-11-18T00:00:00Z',
    steps: [],
    artifacts: [],
    external_tool_invocations: [],
    ...overrides,
  } as unknown as RunDetail
}

const toast: ToastApi = { success: vi.fn(), error: vi.fn(), info: vi.fn() }

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastContext.Provider value={toast}>
        <ConfirmProvider>
          <MemoryRouter initialEntries={['/runs/run-1']}>
            <Routes>
              <Route path="/runs/:runId" element={<RunDetailPage />} />
            </Routes>
          </MemoryRouter>
        </ConfirmProvider>
      </ToastContext.Provider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  MockWebSocket.instances = []
  vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket)
  useAuthStore.setState({
    accessToken: 'token',
    user: { id: 'owner-1', email: 'owner@example.test', role: 'member' } as never,
  })
  vi.mocked(fetchRun).mockResolvedValue(makeRun())
})

describe('RunDetailPage 日志缓冲', () => {
  it('WS 推来的日志在降级轮询（load 重跑）后仍然保留', async () => {
    renderPage()

    // 等首屏加载完、WS 建好
    await waitFor(() => {
      expect(MockWebSocket.instances.length).toBeGreaterThan(0)
    })
    const ws = MockWebSocket.instances[0]
    act(() => ws.open())

    // 推一条日志
    act(() => {
      ws.emit({ type: 'log_line', line: '这条日志必须活下来', level: 'info' })
    })
    await waitFor(() => {
      expect(screen.getByText(/这条日志必须活下来/)).toBeTruthy()
    })

    // 模拟「WS 放弃 -> 降级轮询每 10 秒调一次 load」：
    // 视图内任何一次 load 重跑都不得清空日志
    act(() => {
      ws.close()
    })
    // 让重连退避走完，进入 failed（每 10s 轮询）；这里直接再触发一次 fetchRun 完成
    vi.mocked(fetchRun).mockResolvedValue(makeRun({ current_step: 1 }))
    await waitFor(
      () => {
        expect(fetchRun).toHaveBeenCalledTimes(2)
      },
      { timeout: 3000 }
    )

    // 关键断言：日志还在
    expect(screen.getByText(/这条日志必须活下来/)).toBeTruthy()
  })

  it('切换 run 时仍然清空上一 run 的日志（不能为了保日志而丢掉隔离）', async () => {
    // 用真实导航切换 runId：rerender 时 MemoryRouter 的 initialEntries 不生效，
    // 必须走 navigate 才能真的改变 :runId（组件复用、路由参数变化）
    let goToRun2: (() => void) | null = null
    function NavProbe() {
      const navigate = useNavigate()
      goToRun2 = () => navigate('/runs/run-2')
      return null
    }

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <ToastContext.Provider value={toast}>
          <ConfirmProvider>
            <MemoryRouter initialEntries={['/runs/run-1']}>
              <NavProbe />
              <Routes>
                <Route path="/runs/:runId" element={<RunDetailPage />} />
              </Routes>
            </MemoryRouter>
          </ConfirmProvider>
        </ToastContext.Provider>
      </QueryClientProvider>
    )

    await waitFor(() => expect(MockWebSocket.instances.length).toBeGreaterThan(0))
    act(() => MockWebSocket.instances[0].open())
    act(() => {
      MockWebSocket.instances[0].emit({ type: 'log_line', line: 'run-1 的日志', level: 'info' })
    })
    await waitFor(() => expect(screen.getByText(/run-1 的日志/)).toBeTruthy())

    // 切到另一个 run
    vi.mocked(fetchRun).mockResolvedValue(makeRun({ id: 'run-2' }))
    act(() => goToRun2?.())

    // 旧 run 的日志必须消失
    await waitFor(() => {
      expect(screen.queryByText(/run-1 的日志/)).toBeNull()
    })
  })
})
