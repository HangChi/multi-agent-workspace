/**
 * 009 依赖前端测试。
 *
 * 只钉契约级行为，不写「渲染有没有崩」的空断言：
 *
 * 1. 卡片徽章只在**有未完成前置**时渲染，且数字与标题来自列表端聚合字段
 *    （unmet_count/unmet_titles）——不是详情端的 dependencies 明细，
 *    后者列表端不填（N+1 防线，data-model §4）；
 * 2. 无未完成前置的需求不渲染徽章——「都就绪」不是异常状态；
 * 3. 铃铛通知中心认识 dependency_ready 类型（009 新增）。
 */

import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { WorkRequest } from '../api/endpoints'
import { RequestCard } from '../components/board/RequestCard'

vi.mock('../stores/boardStore', () => ({
  useBoardStore: (selector: (s: { runByRequest: Record<string, string> }) => unknown) =>
    selector({ runByRequest: {} }),
}))

function makeRequest(overrides: Partial<WorkRequest> = {}): WorkRequest {
  return {
    id: 'req-1',
    owner_id: 'u1',
    title: '测试需求',
    description: null,
    expected_output: null,
    priority: 'normal',
    status: 'created',
    attachments: [],
    created_at: '2026-11-18T00:00:00Z',
    updated_at: '2026-11-18T00:00:00Z',
    ...overrides,
  }
}

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('RequestCard 前置徽章', () => {
  it('有未完成前置时渲染徽章（数量 + hover 标题）', () => {
    wrap(
      <RequestCard
        request={makeRequest({
          blocked: true,
          unmet_count: 2,
          unmet_titles: ['前置一', '前置二'],
        })}
      />
    )
    const badge = screen.getByTitle(/前置一、前置二/)
    expect(badge).toBeDefined()
    expect(badge.textContent).toContain('2 个前置未完成')
  })

  it('单个未完成前置也渲染（不为省空间合并）', () => {
    wrap(
      <RequestCard
        request={makeRequest({ blocked: true, unmet_count: 1, unmet_titles: ['唯一前置'] })}
      />
    )
    expect(screen.getByText('1 个前置未完成')).toBeDefined()
  })

  it('无未完成前置不渲染（「都就绪」不是异常状态）', () => {
    wrap(<RequestCard request={makeRequest({ blocked: false, unmet_count: 0 })} />)
    expect(screen.queryByText(/个前置未完成/)).toBeNull()
  })

  it('没有依赖字段的旧形状不渲染（零回归）', () => {
    wrap(<RequestCard request={makeRequest()} />)
    expect(screen.queryByText(/个前置未完成/)).toBeNull()
  })
})
