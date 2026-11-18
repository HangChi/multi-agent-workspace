/**
 * 010 项目成员前端测试。
 *
 * 只钉契约级行为：
 * 1. 成员列表含 owner（is_owner 标记）与成员——owner 不在成员表里，
 *    后端合成，前端直接渲染不自己判断；
 * 2. 非 owner 看不到管理入口（添加器/移除按钮）——成员管理是 owner
 *    特权，前端提前收口比让用户点了吃 403 好；
 * 3. 我的/共享的徽章按 owner_id 判定（唯一归属真相）。
 */

import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from '../components/ui/ConfirmDialog'
import { ToastProvider } from '../components/ui/Toast'

const auth = vi.hoisted(() => ({ userId: 'u-owner' }))

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: auth.userId, email: 'owner@example.test', role: 'member', status: 'active' },
    accessToken: 'token',
    initialized: true,
    isAuthenticated: true,
    isAdmin: false,
    isMember: true,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
  }),
}))

const members = vi.hoisted(() => ({
  items: [] as {
    user_id: string
    user_email: string | null
    is_owner: boolean
    joined_at: string
  }[],
}))

vi.mock('../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/endpoints')>()),
  fetchProjectMembers: vi.fn(async () => ({
    items: members.items,
    total: members.items.length,
  })),
  fetchUserOptions: vi.fn(async () => ({ items: [], total: 0 })),
}))

import { MemberSection } from '../components/project/MemberSection'

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <ConfirmProvider>{ui}</ConfirmProvider>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('MemberSection', () => {
  it('owner 看到创建者徽章与成员，且有添加器', async () => {
    auth.userId = 'u-owner'
    members.items = [
      {
        user_id: 'u-owner',
        user_email: 'owner@example.test',
        is_owner: true,
        joined_at: '2026-11-18T00:00:00Z',
      },
      {
        user_id: 'u-2',
        user_email: 'mate@example.test',
        is_owner: false,
        joined_at: '2026-11-18T00:00:00Z',
      },
    ]
    wrap(<MemberSection projectId="p1" ownerId="u-owner" />)

    expect(await screen.findByText('创建者')).toBeDefined()
    expect(screen.getByText('mate@example.test')).toBeDefined()
    // 添加器与人数
    expect(screen.getByLabelText('选择要添加的成员')).toBeDefined()
    expect(screen.getByText('2 人')).toBeDefined()
  })

  it('非 owner 看不到添加器与移除按钮（管理是 owner 特权）', async () => {
    auth.userId = 'u-2'
    members.items = [
      {
        user_id: 'u-owner',
        user_email: 'owner@example.test',
        is_owner: true,
        joined_at: '2026-11-18T00:00:00Z',
      },
      {
        user_id: 'u-2',
        user_email: 'mate@example.test',
        is_owner: false,
        joined_at: '2026-11-18T00:00:00Z',
      },
    ]
    wrap(<MemberSection projectId="p1" ownerId="u-owner" />)

    await screen.findByText('创建者')
    expect(screen.queryByLabelText('选择要添加的成员')).toBeNull()
    expect(screen.queryByLabelText(/移除成员/)).toBeNull()
    expect(screen.getByText('仅项目创建者可以添加或移除成员。')).toBeDefined()
  })

  it('owner 有移除按钮（对非 owner 成员）', async () => {
    auth.userId = 'u-owner'
    members.items = [
      {
        user_id: 'u-owner',
        user_email: 'owner@example.test',
        is_owner: true,
        joined_at: '2026-11-18T00:00:00Z',
      },
      {
        user_id: 'u-2',
        user_email: 'mate@example.test',
        is_owner: false,
        joined_at: '2026-11-18T00:00:00Z',
      },
    ]
    wrap(<MemberSection projectId="p1" ownerId="u-owner" />)

    await screen.findByText('创建者')
    expect(screen.getByLabelText('移除成员 mate@example.test')).toBeDefined()
    // owner 自己没有移除按钮（后端自移除 409，前端也不给入口）
    expect(screen.queryByLabelText('移除成员 owner@example.test')).toBeNull()
  })

  it('空成员列表只显示 owner 一人', async () => {
    auth.userId = 'u-owner'
    members.items = [
      {
        user_id: 'u-owner',
        user_email: 'owner@example.test',
        is_owner: true,
        joined_at: '2026-11-18T00:00:00Z',
      },
    ]
    wrap(<MemberSection projectId="p1" ownerId="u-owner" />)

    await screen.findByText('创建者')
    expect(screen.getByText('1 人')).toBeDefined()
  })
})
