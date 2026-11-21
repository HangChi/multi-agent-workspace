/**
 * 017 移交弹层的契约级行为。
 *
 * 只钉四件事（其余交给 e2e 与后端集成测试）：
 * 1. 候选成员排除自己——后端对自移交回 409，前端不给走不通的路；
 * 2. 「保留为成员」默认勾选（research R-04 的默认值）；
 * 3. 没选人时确认按钮禁用；
 * 4. 失败时**原样**展示后端 message（同名冲突的解释只在 message 里）。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import { ToastProvider } from '../../ui/Toast'

const auth = vi.hoisted(() => ({ userId: 'u-owner' }))

vi.mock('../../../hooks/useAuth', () => ({
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

vi.mock('../../../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/endpoints')>()),
  fetchProjectMembers: vi.fn(async () => ({
    items: [
      // owner 本人也在成员列表里（后端合成），必须被过滤掉
      { user_id: 'u-owner', user_email: 'owner@example.test', is_owner: true, joined_at: '' },
      { user_id: 'u-mate', user_email: 'mate@example.test', is_owner: false, joined_at: '' },
    ],
    total: 2,
  })),
}))

const transferMock = vi.hoisted(() => vi.fn())

vi.mock('../../../api/projects', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/projects')>()),
  transferProject: transferMock,
}))

import { ProjectTransferDialog } from '../ProjectTransferDialog'

const PROJECT = {
  id: 'p-1',
  owner_id: 'u-owner',
  name: '官网改版',
  description: null,
  status: 'active' as const,
  request_count: 3,
  member_count: 2,
  created_at: '',
  updated_at: '',
}

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('ProjectTransferDialog', () => {
  it('候选里排除自己，只留其他成员', async () => {
    wrap(<ProjectTransferDialog project={PROJECT} open onClose={() => {}} />)

    const select = await screen.findByLabelText('移交给谁')
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'mate@example.test' })).toBeInTheDocument()
    })
    expect(screen.queryByRole('option', { name: 'owner@example.test' })).not.toBeInTheDocument()
    expect(select).toBeInTheDocument()
  })

  it('默认保留成员，未选人时不能提交', async () => {
    wrap(<ProjectTransferDialog project={PROJECT} open onClose={() => {}} />)
    await screen.findByRole('option', { name: 'mate@example.test' })

    expect(screen.getByRole('checkbox')).toBeChecked()
    expect(screen.getByRole('button', { name: '确认移交' })).toBeDisabled()
  })

  it('提交时带上默认的 keep_as_member=true', async () => {
    transferMock.mockResolvedValueOnce({ ...PROJECT, owner_id: 'u-mate' })
    wrap(<ProjectTransferDialog project={PROJECT} open onClose={() => {}} />)
    await screen.findByRole('option', { name: 'mate@example.test' })

    fireEvent.change(screen.getByLabelText('移交给谁'), { target: { value: 'u-mate' } })
    fireEvent.click(screen.getByRole('button', { name: '确认移交' }))

    await waitFor(() => {
      expect(transferMock).toHaveBeenCalledWith('p-1', {
        new_owner_id: 'u-mate',
        keep_as_member: true,
      })
    })
  })

  it('取消勾选后传 keep_as_member=false', async () => {
    transferMock.mockResolvedValueOnce({ ...PROJECT, owner_id: 'u-mate' })
    wrap(<ProjectTransferDialog project={PROJECT} open onClose={() => {}} />)
    await screen.findByRole('option', { name: 'mate@example.test' })

    fireEvent.change(screen.getByLabelText('移交给谁'), { target: { value: 'u-mate' } })
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: '确认移交' }))

    await waitFor(() => {
      expect(transferMock).toHaveBeenCalledWith('p-1', {
        new_owner_id: 'u-mate',
        keep_as_member: false,
      })
    })
  })

  it('后端 message 原样展示（同名冲突的解释只在这里）', async () => {
    transferMock.mockRejectedValueOnce({
      response: {
        data: {
          error: {
            code: 'CONFLICT',
            message: '目标用户已有同名项目「官网改版」，请先改名或换人',
          },
        },
      },
    })
    wrap(<ProjectTransferDialog project={PROJECT} open onClose={() => {}} />)
    await screen.findByRole('option', { name: 'mate@example.test' })

    fireEvent.change(screen.getByLabelText('移交给谁'), { target: { value: 'u-mate' } })
    fireEvent.click(screen.getByRole('button', { name: '确认移交' }))

    expect(
      await screen.findByText('目标用户已有同名项目「官网改版」，请先改名或换人')
    ).toBeInTheDocument()
  })
})
