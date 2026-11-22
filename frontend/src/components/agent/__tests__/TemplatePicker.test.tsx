import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import { fetchAgentTemplates } from '../../../api/endpoints'
import type { AgentTemplate } from '../../../api/endpoints'
import { TemplatePicker } from '../TemplatePicker'

vi.mock('../../../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/endpoints')>()),
  fetchAgentTemplates: vi.fn(),
}))

const builtin: AgentTemplate = {
  id: 'tpl-builtin',
  owner_id: null,
  is_builtin: true,
  visibility: 'team',
  name: '资料检索助手',
  description: '先搜再读、交叉核对',
  system_prompt: 'p',
  model: 'qwen-flash',
  temperature: 0.7,
  max_steps: 10,
  max_run_seconds: 300,
  tools: ['web_search', 'read_url'],
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
  name: '我的数据摘要',
  description: '长材料压成结论',
}

function renderPicker(onPick = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <TemplatePicker open onClose={vi.fn()} onPick={onPick} />
    </QueryClientProvider>
  )
  return { ...utils, onPick }
}

describe('TemplatePicker', () => {
  it('groups builtin and own templates and marks the builtin badge', async () => {
    vi.mocked(fetchAgentTemplates).mockResolvedValue({
      items: [builtin, mine],
      total: 2,
    })
    renderPicker()

    await waitFor(() => expect(screen.getByText('资料检索助手')).toBeInTheDocument())
    expect(screen.getByText('我的数据摘要')).toBeInTheDocument()
    expect(screen.getByText('内置模板（只读）')).toBeInTheDocument()
    // 内置徽章只出现一次（内置那张卡）
    expect(screen.getAllByText('内置')).toHaveLength(1)
  })

  it('filters by search keyword', async () => {
    vi.mocked(fetchAgentTemplates).mockResolvedValue({
      items: [builtin, mine],
      total: 2,
    })
    renderPicker()

    await waitFor(() => expect(screen.getByText('资料检索助手')).toBeInTheDocument())
    fireEvent.change(screen.getByRole('textbox', { name: '搜索模板' }), {
      target: { value: '数据摘要' },
    })
    await waitFor(() =>
      expect(fetchAgentTemplates).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: '数据摘要' })
      )
    )
  })

  it('calls onPick with the selected template and enables the button only after selection', async () => {
    vi.mocked(fetchAgentTemplates).mockResolvedValue({
      items: [builtin, mine],
      total: 2,
    })
    const { onPick } = renderPicker()

    await waitFor(() => expect(screen.getByText('我的数据摘要')).toBeInTheDocument())
    // 未选中时确认按钮禁用——避免误创建
    expect(screen.getByRole('button', { name: '使用这个模板' })).toBeDisabled()

    fireEvent.click(screen.getByText('我的数据摘要'))
    const confirmButton = screen.getByRole('button', { name: '使用这个模板' })
    expect(confirmButton).not.toBeDisabled()
    fireEvent.click(confirmButton)
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 'tpl-mine' }))
  })

  it('shows an empty state when nothing matches', async () => {
    vi.mocked(fetchAgentTemplates).mockResolvedValue({ items: [], total: 0 })
    renderPicker()
    await waitFor(() => expect(screen.getByText('还没有可用模板')).toBeInTheDocument())
  })
})
