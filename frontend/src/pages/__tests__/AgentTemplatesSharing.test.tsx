/**
 * 模板团队共享（023）在前端的可见后果：
 * 1. 别人设为 team 的模板出现在「团队共享」分组里；
 * 2. 那一组的卡片**没有编辑/删除入口**（可用不可改，给了入口也是 403）。
 */

import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from '../../components/ui/ConfirmDialog'
import { ToastProvider } from '../../components/ui/Toast'

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'u-me', email: 'me@example.test', role: 'member', status: 'active' },
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

vi.mock('../../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/endpoints')>()),
  fetchAgentTemplates: vi.fn(async () => ({
    items: [
      {
        id: 't-mine',
        owner_id: 'u-me',
        is_builtin: false,
        visibility: 'private',
        name: '我的私有模板',
        description: null,
        system_prompt: 'p',
        model: 'm',
        temperature: 0.5,
        max_steps: 3,
        max_run_seconds: 60,
        tools: [],
        cost_limit_cents: 100,
        egress_mode: 'default',
        created_at: '',
        updated_at: '',
      },
      {
        id: 't-team',
        owner_id: 'u-other',
        is_builtin: false,
        visibility: 'team',
        name: '同事的团队模板',
        description: null,
        system_prompt: 'p',
        model: 'm',
        temperature: 0.5,
        max_steps: 3,
        max_run_seconds: 60,
        tools: [],
        cost_limit_cents: 100,
        egress_mode: 'default',
        created_at: '',
        updated_at: '',
      },
    ],
    total: 2,
  })),
  fetchTemplateVersions: vi.fn(async () => []),
}))

import { AgentTemplatesPage } from '../AgentTemplatesPage'

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

describe('AgentTemplatesPage 团队共享', () => {
  it('团队模板进「团队共享」分组，且只有一张「用这个模板」按钮', async () => {
    wrap(<AgentTemplatesPage />)

    expect(await screen.findByText('同事的团队模板')).toBeInTheDocument()
    // 「团队共享」既是分组标题也是卡片徽章，用 getAll
    expect(screen.getAllByText('团队共享').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('我的私有模板')).toBeInTheDocument()

    // 两张卡片都能「用这个模板」，但只有自己那张有编辑/历史入口
    expect(screen.getAllByRole('button', { name: '用这个模板' })).toHaveLength(2)
    expect(screen.getAllByRole('button', { name: '编辑' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: '历史 / 共享' })).toHaveLength(1)
    // 026：团队卡片可「复制到我的」，自己的卡片不需要（复制自己没意义）
    expect(screen.getAllByRole('button', { name: '复制到我的' })).toHaveLength(1)
  })
})
