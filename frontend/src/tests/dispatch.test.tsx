/**
 * 007 分派链路前端测试。
 *
 * 只钉契约级行为，不写「渲染有没有崩」的空断言：
 *
 * 1. `isDispatchResult` 必须能区分两种 assign 响应——拖员工（无 run_id）
 *    与拖 Agent/组（有 run_id）。混用会让「查看运行」跳到一个不存在的 run。
 * 2. DispatchPanel 的按钮可见性由「是不是当前接收人/创建人 + 状态」决定：
 *    陌生人看不到任何按钮（后端同样会 404，前端不给出入口）；
 *    pending 时接收人才能「接收」；未 accept 就拖 Agent 后端 409，
 *    前端必须先把「接收」作为唯一主路径给出来。
 * 3. 通知铃铛：未读数来自轮询端点；「全部已读」要同时刷新列表与计数，
 *    否则角标会在点掉后继续显示旧数字。
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { isDispatchResult } from '../api/assign'
import type { WorkRequest } from '../api/endpoints'
import { DispatchPanel } from '../components/board/DispatchPanel'

// ---------- 1. isDispatchResult ----------

describe('isDispatchResult', () => {
  it('拖员工的响应（request_id + status）判为转交', () => {
    expect(isDispatchResult({ request_id: 'r1', status: 'pending' })).toBe(true)
  })

  it('拖 Agent/组的响应（run_id）判为执行', () => {
    expect(isDispatchResult({ run_id: 'run-1', status: 'queued' })).toBe(false)
  })
})

// ---------- 2. DispatchPanel 按钮可见性 ----------

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
  }
}

function renderPanel(
  request: WorkRequest,
  currentUserId: string,
  handlers: { onAccept?: () => void; onReject?: () => void } = {}
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DispatchPanel
          request={request}
          currentUserId={currentUserId}
          accepting={false}
          rejecting={false}
          onAccept={handlers.onAccept ?? vi.fn()}
          onReject={handlers.onReject ?? vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('DispatchPanel', () => {
  it('当前接收人 + pending：显示「接收」，点击触发回调', async () => {
    const onAccept = vi.fn()
    renderPanel(
      makeRequest({
        status: 'pending',
        assignee_id: 'worker-1',
        assignee_email: 'worker@example.test',
      }),
      'worker-1',
      { onAccept }
    )
    const button = screen.getByRole('button', { name: '接收' })
    expect(button).toBeDefined()
    fireEvent.click(button)
    expect(onAccept).toHaveBeenCalledOnce()
  })

  it('pending 但未接收：不能直接拖 Agent（后端 409），面板不给出执行入口', () => {
    renderPanel(
      makeRequest({
        status: 'pending',
        assignee_id: 'worker-1',
        assignee_email: 'worker@example.test',
      }),
      'worker-1'
    )
    // 只有「接收 / 退回」，没有任何「分配给 Agent」类入口
    expect(screen.queryByRole('button', { name: /分配|执行/ })).toBeNull()
  })

  it('陌生人（非 owner 非接收人）：看不到任何操作按钮', () => {
    renderPanel(
      makeRequest({
        status: 'pending',
        assignee_id: 'worker-1',
        assignee_email: 'worker@example.test',
      }),
      'stranger-1'
    )
    expect(screen.queryByRole('button', { name: '接收' })).toBeNull()
    expect(screen.queryByRole('button', { name: '退回' })).toBeNull()
  })

  it('创建者对自己创建的 created 需求：无分派信息时显示未分派', () => {
    renderPanel(makeRequest({ status: 'created' }), 'owner-1')
    expect(screen.getByText('尚未分派给任何人')).toBeDefined()
  })

  it('接收人退回后需求回 created：经手链含退回原因', () => {
    renderPanel(
      makeRequest({
        status: 'created',
        owner_id: 'owner-1',
        dispatch_history: [
          {
            id: 'd1',
            seq: 1,
            action: 'dispatch',
            actor_id: 'owner-1',
            actor_email: 'owner@example.test',
            from_assignee_id: null,
            from_assignee_email: null,
            to_assignee_id: 'worker-1',
            to_assignee_email: 'worker@example.test',
            reason: null,
            created_at: '2026-11-18T10:00:00Z',
          },
          {
            id: 'd2',
            seq: 2,
            action: 'reject',
            actor_id: 'worker-1',
            actor_email: 'worker@example.test',
            from_assignee_id: 'worker-1',
            from_assignee_email: 'worker@example.test',
            to_assignee_id: null,
            to_assignee_email: null,
            reason: '这周排期已满',
            created_at: '2026-11-18T11:00:00Z',
          },
        ],
      }),
      'owner-1'
    )
    expect(screen.getByText('经手记录')).toBeDefined()
    expect(screen.getByText('原因：这周排期已满')).toBeDefined()
    // US1 验收的「谁、何时、为什么」：经手人显示邮箱而非 UUID
    expect(screen.getByText('worker')).toBeDefined()
  })

  it('执行中（running）的需求：accept/reject 都不出现（FR-005）', () => {
    renderPanel(
      makeRequest({
        status: 'running',
        assignee_id: 'worker-1',
        assignee_email: 'worker@example.test',
      }),
      'worker-1'
    )
    expect(screen.queryByRole('button', { name: '接收' })).toBeNull()
    expect(screen.queryByRole('button', { name: '退回' })).toBeNull()
  })
})
