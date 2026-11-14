import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../../../api/client'
import { EgressRules } from '../EgressRules'

vi.mock('../../../api/client', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}))

describe('EgressRules', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [], egress_mode: 'restricted' } })
  })

  it('renders existing rules and adds a domain rule', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { items: [{ id: 'r1', target_type: 'domain', target: 'pypi.org', port_range: '443', note: null }], egress_mode: 'restricted' } })
    render(<EgressRules agentId="agent-1" mode="restricted" />)
    await waitFor(() => expect(screen.getByText('pypi.org')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('域名或网段'), { target: { value: 'api.example.com' } })
    fireEvent.click(screen.getByRole('button', { name: '添加规则' }))
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith('/agents/agent-1/egress-rules', expect.objectContaining({ target_type: 'domain', target: 'api.example.com' })))
  })
})
