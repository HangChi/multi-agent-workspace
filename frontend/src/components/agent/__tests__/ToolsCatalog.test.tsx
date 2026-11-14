import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { fetchTools } from '../../../api/endpoints'
import { AgentForm } from '../AgentForm'

vi.mock('../../../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/endpoints')>()),
  fetchTools: vi.fn(),
}))

describe('AgentForm tool catalog', () => {
  it('renders enabled catalog entries and omits disabled entries', async () => {
    vi.mocked(fetchTools).mockResolvedValue({ items: [
      { name: 'run_python', display_name: '执行 Python', description: '沙箱执行', kind: 'sandbox', enabled: true, param_schema: {}, timeout_seconds: 120 },
      { name: 'legacy', display_name: '旧工具', description: 'disabled', kind: 'builtin', enabled: false, param_schema: {}, timeout_seconds: 30 },
    ], total: 1 })
    render(<AgentForm onSubmit={vi.fn()} onCancel={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('执行 Python')).toBeInTheDocument())
    expect(screen.queryByText('旧工具')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run_python/ })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(screen.getByRole('button', { name: /run_python/ }))
    expect(screen.getByRole('button', { name: /run_python/ })).toHaveAttribute('aria-pressed', 'true')
  })
})
