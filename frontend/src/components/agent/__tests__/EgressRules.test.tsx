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

  it.each([
    ['203.0.113.10', 'ip'],
    ['2001:db8::1', 'ip'],
    ['::1', 'ip'],
    ['::ffff:192.0.2.1', 'ip'],
    ['203.0.113.0/24', 'cidr'],
    ['2001:db8::/32', 'cidr'],
  ])('saves %s with the correct target type %s', async (target, targetType) => {
    render(<EgressRules agentId="agent-1" mode="restricted" />)
    fireEvent.change(screen.getByLabelText('域名或网段'), { target: { value: target } })
    fireEvent.click(screen.getByRole('button', { name: '添加规则' }))
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith(
      '/agents/agent-1/egress-rules', expect.objectContaining({ target_type: targetType, target }),
    ))
  })

  it('explains controlled DNS domain rules without promising application identity checks', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { items: [{ id: 'r1', target_type: 'domain', target: 'pypi.org', port_range: null }], egress_mode: 'restricted' } })
    render(<EgressRules agentId="agent-1" mode="restricted" />)
    await screen.findByText('pypi.org')
    expect(screen.getByText(/仅允许清单内/)).toHaveTextContent('域名、IP/网段和端口')
    expect(screen.getByRole('status')).toHaveTextContent('受控 DNS')
    expect(screen.getByRole('status')).toHaveTextContent('TTL')
    expect(screen.getByRole('status')).toHaveTextContent('不做 TLS 解密')
    expect(screen.getByRole('status')).toHaveTextContent('不等同于应用层域名身份校验')
    expect(screen.queryByText(/域名规则当前不支持/)).not.toBeInTheDocument()
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
