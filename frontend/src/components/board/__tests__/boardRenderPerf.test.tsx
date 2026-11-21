/**
 * 看板渲染性能回归（P7，docs/code-audit-2026-11-21.md）。
 *
 * 这两条都不是「功能对不对」的测试——功能本来就对。它们测的是**不必要的
 * 重渲染**，而 React.memo 的跳过行为无法从 DOM 上观察到（跳过的组件照样
 * 留在 DOM 里），所以分两层验证：
 *
 * 1. 结构层：`RequestCard` 必须真的是 memo 组件。回退 memo 后这条立刻变红。
 * 2. 行为层：`onOpen` 必须收到 request 本身（签名从闭包改为传参），
 *    这是父组件能传**同一个**函数引用的前提；否则 memo 的浅比较永远不等。
 */

import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import type { WorkRequest } from '../../../api/endpoints'
import { RequestCard } from '../RequestCard'

function makeRequest(overrides: Partial<WorkRequest> = {}): WorkRequest {
  return {
    id: 'r-1',
    title: '测试需求',
    description: null,
    expected_output: null,
    priority: 'normal',
    status: 'created',
    owner_id: 'u-1',
    owner_email: 'a@b.com',
    project_id: null,
    project_name: null,
    attachments: [],
    // 009：列表端聚合字段。缺了 unmet_titles 卡片会在 `?.length` 之外炸掉
    // （见过 TypeError: Cannot read properties of undefined (reading 'length')）
    blocked: false,
    unmet_count: 0,
    unmet_titles: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  } as WorkRequest
}

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, enabled: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('RequestCard 的 memo', () => {
  it('必须是 memo 组件（否则看板拖拽每帧重渲染全部卡片）', () => {
    // React.memo 产物的 $$typeof 是 Symbol(react.memo)。
    // 回退成普通函数组件后这条直接变红——这是「memo 还在不在」的硬断言，
    // 不依赖任何运行时观察，也就不会写成永远为绿的空测试。
    const marker = (RequestCard as unknown as { $$typeof?: symbol }).$$typeof
    expect(String(marker)).toBe('Symbol(react.memo)')
  })

  it('onOpen 收到 request 本身（签名从闭包改为传参）', () => {
    const request = makeRequest()
    let received: WorkRequest | null = null
    wrap(
      <RequestCard
        request={request}
        onOpen={(r) => {
          received = r
        }}
      />
    )
    screen.getByText('测试需求').click()
    // 旧签名是 onOpen() 无参，闭包在父组件里捕获 r；那样父组件无法传
    // 同一个引用（每张卡一个箭头函数），memo 白包。
    expect((received as WorkRequest | null)?.id).toBe(request.id)
  })

  it('「详情」按钮同样传 request', () => {
    const request = makeRequest()
    let received: WorkRequest | null = null
    wrap(
      <RequestCard
        request={request}
        onOpen={(r) => {
          received = r
        }}
      />
    )
    screen.getByRole('button', { name: '详情' }).click()
    expect((received as WorkRequest | null)?.id).toBe(request.id)
  })
})
