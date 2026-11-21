/**
 * 019 项目成员角色在前端的两个可见后果：
 * 1. 只读成员在列表里显示「只读」徽章，而不是笼统的「成员」；
 * 2. owner 加人时可以选角色，且 **viewer 要原样传给后端**（不能悄悄退回 editor）。
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

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

const fetchMock = vi.hoisted(() => vi.fn())
const addMock = vi.hoisted(() => vi.fn(async () => ({})))

vi.mock('../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/endpoints')>()),
  fetchProjectMembers: fetchMock,
  fetchUserOptions: vi.fn(async () => ({
    items: [
      { id: 'u-viewer', email: 'viewer@example.test' },
      { id: 'u-new', email: 'new@example.test' },
    ],
    total: 2,
  })),
  addProjectMember: addMock,
  removeProjectMember: vi.fn(),
}))

import { MemberSection } from '../components/project/MemberSection'

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>{ui}</ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('MemberSection 角色', () => {
  it('只读成员显示「只读」徽章', async () => {
    fetchMock.mockResolvedValue({
      items: [
        { user_id: 'u-owner', user_email: 'owner@example.test', is_owner: true, role: 'owner', joined_at: '' },
        { user_id: 'u-viewer', user_email: 'viewer@example.test', is_owner: false, role: 'viewer', joined_at: '' },
      ],
      total: 2,
    })
    wrap(<MemberSection projectId="p-1" ownerId="u-owner" />)

    // 先确认数据真的进来了（不然「找不到徽章」是数据问题而不是渲染问题）
    expect(await screen.findByText('viewer@example.test')).toBeInTheDocument()
    expect(screen.getByText('owner@example.test')).toBeInTheDocument()
    // 限定在成员列表内断言：添加器的角色下拉里也有「只读」这个 option
    const list = screen.getByRole('list')
    expect(within(list).getByText('只读')).toBeInTheDocument()
    expect(within(list).getByText('创建者')).toBeInTheDocument()
  })

  it('添加时选「只读」，传给后端的 role 是 viewer', async () => {
    fetchMock.mockResolvedValue({
      items: [
        { user_id: 'u-owner', user_email: 'owner@example.test', is_owner: true, role: 'owner', joined_at: '' },
      ],
      total: 1,
    })
    wrap(<MemberSection projectId="p-1" ownerId="u-owner" />)

    await screen.findByText('创建者')
    fireEvent.change(screen.getByLabelText('选择要添加的成员'), {
      target: { value: 'u-new' },
    })
    fireEvent.change(screen.getByLabelText('新成员的角色'), {
      target: { value: 'viewer' },
    })
    fireEvent.click(screen.getByRole('button', { name: '添加' }))

    await waitFor(() => {
      expect(addMock).toHaveBeenCalledWith('p-1', 'u-new', 'viewer')
    })
  })
})
