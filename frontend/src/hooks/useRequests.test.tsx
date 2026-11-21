/**
 * 需求乐观更新的缓存结构兼容性测试（docs/code-audit-2026-11-21.md D1）。
 *
 * 背景：`['requests']` 前缀下混着两种结构完全不同的缓存——
 * - 列表：`['requests', scope, project]` → `{ items: WorkRequest[] }`
 * - 详情：`['requests', 'detail', id]` → **单个** WorkRequest（无 items）
 *
 * 早前 `useAssignRequest.onMutate` 无条件走 `data.items.map(...)`。从通知深链
 * 进入详情（详情被单独缓存）后再拖任意卡片，`data.items` 是 undefined，
 * `undefined.map` 抛 TypeError → onMutate 失败 → 拖拽毫无反馈。
 *
 * 本测试直接断言 onMutate 的行为，不渲染整页：这类缓存形状问题是纯数据逻辑，
 * 挂在看板组件上写反而测不到（看板只挂载列表 key）。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkRequest } from '../api/endpoints'
import { useAssignRequest } from './useRequests'

// useRequests 从 barrel '../api/endpoints' 取 assignRequest，这里 mock 同一模块同名导出
vi.mock('../api/endpoints', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/endpoints')>()
  return { ...actual, assignRequest: vi.fn() }
})

import { assignRequest } from '../api/endpoints'

function makeRequest(overrides: Partial<WorkRequest> = {}): WorkRequest {
  return {
    id: 'req-1',
    owner_id: 'owner-1',
    title: '测试需求',
    description: null,
    expected_output: null,
    priority: 'normal',
    status: 'created',
    attachments: [],
    created_at: '2026-11-18T00:00:00Z',
    updated_at: '2026-11-18T00:00:00Z',
    ...overrides,
  } as WorkRequest
}

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('useAssignRequest 乐观更新与缓存结构', () => {
  it('缓存里同时有列表与详情时，不崩溃且只改列表', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    // 列表型缓存（真正该被乐观更新）
    const listKey = ['requests', 'relevant', null]
    const listData = { items: [makeRequest()], total: 1 }
    client.setQueryData(listKey, listData)

    // 详情型缓存（单对象，没有 items）——这是崩溃的诱因
    const detailKey = ['requests', 'detail', 'req-1']
    const detailData = makeRequest({ title: '详情里的需求' })
    client.setQueryData(detailKey, detailData)

    // 分配成功（agent 落点）
    vi.mocked(assignRequest).mockResolvedValue({ run_id: 'run-1', status: 'queued' } as never)

    const { result } = renderHook(() => useAssignRequest(), { wrapper: wrapper(client) })

    // 关键断言：不抛异常
    await expect(
      result.current.assignAsync({
        requestId: 'req-1',
        payload: { assignee_type: 'agent', assignee_id: 'a-1' },
        assigneeLabel: 'Agent One',
      })
    ).resolves.not.toThrow()

    // 列表被乐观更新成 assigned（然后在 onSuccess 里被 invalidate，但缓存值已被打上）
    const after = client.getQueryData<{ items: WorkRequest[] }>(listKey)
    expect(after?.items[0]?.status).toBe('assigned')
    expect(after?.items[0]?.assignee_id).toBe('a-1')

    // 详情缓存必须原样保留——它没有 items，不该被列表逻辑改写
    const detailAfter = client.getQueryData<WorkRequest>(detailKey)
    expect(detailAfter).toEqual(detailData)
  })

  it('只有详情缓存时也不崩溃（深链刚打开、列表还没到位）', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    client.setQueryData(['requests', 'detail', 'req-1'], makeRequest())
    vi.mocked(assignRequest).mockResolvedValue({ run_id: 'run-1', status: 'queued' } as never)

    const { result } = renderHook(() => useAssignRequest(), { wrapper: wrapper(client) })

    await expect(
      result.current.assignAsync({
        requestId: 'req-1',
        payload: { assignee_type: 'agent', assignee_id: 'a-1' },
      })
    ).resolves.not.toThrow()

    // 详情仍是原对象，没有被写坏
    expect(client.getQueryData(['requests', 'detail', 'req-1'])).toEqual(makeRequest())
  })

  it('失败时回滚所有快照（含详情），不留下乐观态', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const listKey = ['requests', 'relevant', null]
    client.setQueryData(listKey, { items: [makeRequest()], total: 1 })
    client.setQueryData(['requests', 'detail', 'req-1'], makeRequest())

    vi.mocked(assignRequest).mockRejectedValue(new Error('boom'))

    const { result } = renderHook(() => useAssignRequest(), { wrapper: wrapper(client) })

    await result.current
      .assignAsync({
        requestId: 'req-1',
        payload: { assignee_type: 'agent', assignee_id: 'a-1' },
      })
      .catch(() => undefined)

    await waitFor(() => {
      const after = client.getQueryData<{ items: WorkRequest[] }>(listKey)
      expect(after?.items[0]?.status).toBe('created')
    })
  })
})
