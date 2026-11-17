import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { deleteAgentTemplate, fetchAgentTemplates, updateAgentTemplate } from '../../api/endpoints'
import type { AgentTemplate } from '../../api/endpoints'
import { AgentTemplatesPage } from '../AgentTemplatesPage'
import { ConfirmProvider } from '../../components/ui/ConfirmDialog'
import { ToastProvider } from '../../components/ui/Toast'

vi.mock('../../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/endpoints')>()),
  fetchAgentTemplates: vi.fn(),
  updateAgentTemplate: vi.fn(),
  deleteAgentTemplate: vi.fn(),
}))

const builtin: AgentTemplate = {
  id: 'tpl-builtin',
  owner_id: null,
  is_builtin: true,
  name: '代码审查助手',
  description: '按严重程度分组输出审查意见',
  system_prompt: '你是审查助手',
  model: 'Deepseek-V4.1-Flash',
  temperature: 0.4,
  max_steps: 10,
  max_run_seconds: 300,
  tools: ['run_python'],
  cost_limit_cents: 500,
  egress_mode: 'default',
  created_at: '2026-11-18T00:00:00Z',
  updated_at: '2026-11-18T00:00:00Z',
}

const mine: AgentTemplate = {
  ...builtin,
  id: 'tpl-mine',
  owner_id: 'me',
  is_builtin: false,
  name: '团队周报助手',
  description: '自己沉淀的',
}

// 页面用了 toast/confirm/useAuth 三个 context，测试必须如实包上——
// 少包一个只会得到「必须在 Provider 内使用」的红，而不是行为断言。
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'me', email: 'me@example.test', role: 'member', status: 'active' },
    initialized: true,
    isAuthenticated: true,
    isAdmin: false,
    accessToken: 'token',
    logout: vi.fn(),
  }),
}))

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ConfirmProvider>
          <MemoryRouter>
            <AgentTemplatesPage />
          </MemoryRouter>
        </ConfirmProvider>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('AgentTemplatesPage', () => {
  it('renders builtin and own templates, with builtin marked read-only', async () => {
    vi.mocked(fetchAgentTemplates).mockResolvedValue({
      items: [builtin, mine],
      total: 2,
    })
    renderPage()

    await waitFor(() => expect(screen.getByText('代码审查助手')).toBeInTheDocument())
    expect(screen.getByText('团队周报助手')).toBeInTheDocument()
    // 两个分区标题都在
    expect(screen.getByText('内置模板（只读）')).toBeInTheDocument()
    expect(screen.getByText('团队周报助手')).toBeInTheDocument()
    // 内置卡片给出只读说明，且没有编辑/删除按钮
    expect(
      screen.getByText(/内置模板不可修改；需要改就先「用这个模板」创建 Agent/)
    ).toBeInTheDocument()
    const editButtons = screen.getAllByRole('button', { name: '编辑' })
    expect(editButtons).toHaveLength(1) // 只有「我的模板」那张卡有
  })

  it('supports editing an own template', async () => {
    vi.mocked(fetchAgentTemplates).mockResolvedValue({ items: [mine], total: 1 })
    vi.mocked(updateAgentTemplate).mockResolvedValue({ ...mine, description: '改过了' })
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '编辑' }))
    // 弹层标题指明在编辑哪个模板
    expect(screen.getByText('编辑模板：团队周报助手')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: /描述/ }), {
      target: { value: '改过了' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存模板' }))
    await waitFor(() =>
      expect(updateAgentTemplate).toHaveBeenCalledWith(
        'tpl-mine',
        expect.objectContaining({ description: '改过了' })
      )
    )
  })

  it('deletes an own template after confirmation', async () => {
    vi.mocked(fetchAgentTemplates).mockResolvedValue({ items: [mine], total: 1 })
    vi.mocked(deleteAgentTemplate).mockResolvedValue(undefined)
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: '删除' }))
    // 确认框把后果说清：不可恢复，但已由它创建的 Agent 不受影响
    await waitFor(() => expect(screen.getByText(/已由它创建的 Agent 不受影响/)).toBeInTheDocument())
    // 确认框里的「删除」按钮（Modal 内）才是真正提交的那个
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    await waitFor(() => expect(deleteAgentTemplate).toHaveBeenCalledWith('tpl-mine'))
  })

  it('shows an empty state when there is no template at all', async () => {
    vi.mocked(fetchAgentTemplates).mockResolvedValue({ items: [], total: 0 })
    renderPage()
    await waitFor(() => expect(screen.getByText('还没有模板')).toBeInTheDocument())
    expect(screen.getByText(/另存为模板/)).toBeInTheDocument()
  })

  it('opens a prefilled Agent form when using a template', async () => {
    vi.mocked(fetchAgentTemplates).mockResolvedValue({ items: [builtin], total: 1 })
    renderPage()

    fireEvent.click((await screen.findAllByRole('button', { name: '用这个模板' }))[0])
    // 表单被模板预填：标题说明来源，提示词是模板的值
    await waitFor(() => expect(screen.getByText('用模板创建：代码审查助手')).toBeInTheDocument())
    expect(screen.getByRole('textbox', { name: /系统提示词/ })).toHaveValue('你是审查助手')
  })
})
