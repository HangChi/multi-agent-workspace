/**
 * 008 项目容器前端测试。
 *
 * 只钉契约级行为，不写「渲染有没有崩」的空断言：
 *
 * 1. 建需求表单的项目选择器**默认不归类**——项目是可选组织维度，
 *    逼用户先建项目才能建需求是把容器当门禁（spec Assumptions 的依据）；
 * 2. 已归档项目**不可选**（后端对归档项目 400，前端先过滤掉——
 *    两边都拦是因为「选项里看得见但提交就红」是最差的一种失败）；
 * 3. 看板项目下拉含「未归属」选项（FR-006：project=none 是产品功能，
 *    不是查询技巧——「还没归类的活」要能被单独审视）；
 * 4. viewer 对项目管理页无写入口（后端 403，前端直接渲染 403 态不发请求）。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from '../components/ui/ConfirmDialog'
import { ToastProvider } from '../components/ui/Toast'

import type { Project } from '../api/endpoints'
import { RequestForm } from '../components/request/RequestForm'

const auth = vi.hoisted(() => ({ role: 'member' as 'admin' | 'member' | 'viewer' }))

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'u1', email: 'ops@example.test', role: auth.role, status: 'active' },
    accessToken: 'token',
    initialized: true,
    isAuthenticated: true,
    isAdmin: auth.role === 'admin',
    isMember: auth.role !== 'viewer',
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
  }),
}))

const projects = vi.hoisted(() => ({
  items: [] as Project[],
}))

vi.mock('../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/endpoints')>()),
  fetchProjects: vi.fn(async () => ({ items: projects.items, total: projects.items.length })),
}))

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

function renderForm() {
  const onSubmit = vi.fn()
  wrap(<RequestForm onSubmit={onSubmit} onCancel={vi.fn()} />)
  return { onSubmit }
}

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    owner_id: 'u1',
    name: '官网改版',
    description: null,
    status: 'active',
    request_count: 3,
    created_at: '2026-11-18T00:00:00Z',
    updated_at: '2026-11-18T00:00:00Z',
    ...overrides,
  }
}

describe('RequestForm 项目选择器', () => {
  it('默认「不归类」——项目可选，不是建需求的前置门禁', async () => {
    projects.items = [makeProject()]
    const { onSubmit } = renderForm()

    const select = (await screen.findByLabelText('所属项目（可选）')) as HTMLSelectElement
    expect(select.value).toBe('') // 「不归类」是默认项

    // 不碰项目直接提交 → project_id 为 null
    fireEvent.change(screen.getByLabelText(/标题/), {
      target: { value: '一条不归类的需求' },
    })
    fireEvent.click(screen.getByRole('button', { name: /创建需求/ }))
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ project_id: null }))
    })
  })

  it('已归档项目不可选（后端 400，前端先过滤）', async () => {
    projects.items = [
      makeProject({ id: 'p-active', name: '活跃项目' }),
      makeProject({ id: 'p-archived', name: '已归档项目', status: 'archived' }),
    ]
    renderForm()

    // 选项来自异步查询：等到「活跃项目」出现才算数据到位
    // （findByLabelText 只等 select 元素出现，不等它的 options）
    await screen.findByRole('option', { name: /活跃项目/ })
    const select = screen.getByLabelText('所属项目（可选）') as HTMLSelectElement
    const optionTexts = Array.from(select.options).map((o) => o.textContent ?? '')
    expect(optionTexts.some((t) => t.includes('活跃项目'))).toBe(true)
    expect(optionTexts.some((t) => t.includes('已归档项目'))).toBe(false)
  })

  it('选中项目后提交带 project_id', async () => {
    projects.items = [makeProject()]
    const { onSubmit } = renderForm()

    const select = (await screen.findByLabelText('所属项目（可选）')) as HTMLSelectElement
    await screen.findByRole('option', { name: /官网改版/ })
    fireEvent.change(select, { target: { value: 'p1' } })

    fireEvent.change(screen.getByLabelText(/标题/), {
      target: { value: '归类的需求' },
    })
    fireEvent.click(screen.getByRole('button', { name: /创建需求/ }))
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ project_id: 'p1' }))
    })
  })
})

// ---------- 看板项目下拉（T021 的契约） ----------

describe('Board 项目筛选下拉', () => {
  it('含「未归属」选项——未归类的活要能被单独审视（FR-006）', async () => {
    const { Board } = await import('../components/board/Board')
    projects.items = [makeProject()]

    wrap(
      <Board
        requests={[]}
        agents={[]}
        groups={[]}
        users={[]}
        projects={projects.items}
        project={null}
        onProjectChange={vi.fn()}
        onAssignStart={vi.fn()}
      />
    )

    // 「全部项目」与「未归属」是常驻项（有项目时才渲染下拉）
    await screen.findByRole('option', { name: /官网改版/ })
    expect(screen.getByRole('option', { name: '未归属' })).toBeDefined()
    expect(screen.getByRole('option', { name: '全部项目' })).toBeDefined()
  })

  it('无项目时整个下拉不渲染——空下拉只会分散注意力', async () => {
    const { Board } = await import('../components/board/Board')
    projects.items = []

    wrap(
      <Board
        requests={[]}
        agents={[]}
        groups={[]}
        users={[]}
        projects={[]}
        project={null}
        onProjectChange={vi.fn()}
        onAssignStart={vi.fn()}
      />
    )

    expect(screen.queryByLabelText('按项目筛选')).toBeNull()
  })
})

// ---------- viewer 无入口（T019 的契约） ----------

describe('ProjectsPage 角色门', () => {
  it('viewer 渲染 403 态且不发列表请求', async () => {
    auth.role = 'viewer'
    const { fetchProjects } = await import('../api/endpoints')
    vi.mocked(fetchProjects).mockClear()

    const { ProjectsPage } = await import('../pages/ProjectsPage')
    wrap(<ProjectsPage />)

    expect(await screen.findByText('无权访问')).toBeDefined()
    // 明知 403 就不发请求（同 SchedulesPage 纪律：少一条红色噪声）
    expect(fetchProjects).not.toHaveBeenCalled()

    auth.role = 'member' // 复位，避免污染同文件其他用例
  })
})
