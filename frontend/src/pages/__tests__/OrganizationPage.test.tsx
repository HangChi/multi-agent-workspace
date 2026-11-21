/**
 * 组织架构页（020）的契约级测试。
 *
 * 只钉两条：部门树按 path 顺序渲染（缩进靠 depth）、新建提交带上父部门与范围。
 * 分派门禁的判定在后端（11 例集成测试），页面不重复实现逻辑。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from '../../components/ui/ConfirmDialog'
import { ToastProvider } from '../../components/ui/Toast'

const createMock = vi.hoisted(() => vi.fn(async () => ({})))

vi.mock('../../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/endpoints')>()),
  fetchDepartments: vi.fn(async () => [
    {
      id: 'd-root',
      name: '研发中心',
      parent_id: null,
      path: '/d-root/',
      depth: 0,
      assign_scope: 'subtree',
      member_count: 3,
      created_at: '',
    },
    {
      id: 'd-child',
      name: '平台组',
      parent_id: 'd-root',
      path: '/d-root/d-child/',
      depth: 1,
      assign_scope: 'self',
      member_count: 2,
      created_at: '',
    },
  ]),
  fetchDepartmentMembers: vi.fn(async () => [
    { user_id: 'u-1', email: 'a@example.test', department_id: 'd-root' },
  ]),
  createDepartment: createMock,
  updateDepartment: vi.fn(),
  deleteDepartment: vi.fn(),
  setUserDepartment: vi.fn(),
}))

import { OrganizationPage } from '../OrganizationPage'

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

describe('OrganizationPage', () => {
  it('按 path 顺序渲染部门与分派范围', async () => {
    wrap(<OrganizationPage />)
    // 部门名会同时出现在树列表与两个下拉（上级 / 人员归属）里，用 getAll
    expect((await screen.findAllByText('研发中心')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('平台组').length).toBeGreaterThan(0)
    // 范围文案来自 SCOPE_LABEL（与后端三档一一对应）
    expect(screen.getAllByText('本部门及下级').length).toBeGreaterThan(0)
    expect(screen.getByText('2 人')).toBeInTheDocument()
  })

  it('新建时把上级与范围一起提交', async () => {
    wrap(<OrganizationPage />)
    await screen.findAllByText('研发中心')

    fireEvent.change(screen.getByLabelText('部门名'), { target: { value: '新部门' } })
    fireEvent.change(screen.getByLabelText('上级部门'), { target: { value: 'd-root' } })
    fireEvent.change(screen.getByLabelText('分派范围'), { target: { value: 'self' } })
    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(createMock).toHaveBeenCalledWith({
        name: '新部门',
        parent_id: 'd-root',
        assign_scope: 'self',
      })
    })
  })
})
