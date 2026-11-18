import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import {
  createExternalToolGrant,
  fetchExternalServers,
  fetchExternalToolGrants,
  fetchMarketplaceInstallations,
} from '../../../api/endpoints'
import { ExternalToolGrants } from '../ExternalToolGrants'
import { ToastProvider } from '../../ui/Toast'

vi.mock('../../../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/endpoints')>()),
  createExternalToolGrant: vi.fn(),
  fetchExternalServers: vi.fn(),
  fetchExternalToolGrants: vi.fn(),
  fetchMarketplaceInstallations: vi.fn(),
  revokeExternalToolGrant: vi.fn(),
}))

describe('ExternalToolGrants', () => {
  it('grants a pinned installed version without merging into builtin tools', async () => {
    vi.mocked(fetchExternalServers).mockResolvedValue({
      items: [
        {
          id: 'server-1',
          name: 'Fixture',
          endpoint_url: 'https://safe.example.test',
          maintainer: 'Team',
          status: 'pending_review',
          current_validation_status: 'passed',
          tools: [
            {
              id: 'version-1',
              external_name: 'lookup',
              stable_name: 'mcp_fixture_lookup',
              version: '1.0.0',
              description: 'lookup',
              availability: 'valid',
              required_permissions: [],
              listing_id: 'listing-1',
              listing_status: 'published',
            },
          ],
        },
      ],
      total: 1,
    })
    vi.mocked(fetchMarketplaceInstallations).mockResolvedValue({
      items: [
        {
          id: 'install-1',
          listing_id: 'listing-1',
          owner_id: 'owner-1',
          status: 'installed',
          version_policy: 'pinned',
          installed_at: 'now',
        },
      ],
      total: 1,
    })
    vi.mocked(fetchExternalToolGrants).mockResolvedValue({ items: [], total: 0 })
    vi.mocked(createExternalToolGrant).mockResolvedValue({
      id: 'grant-1',
      agent_id: 'agent-1',
      installation_id: 'install-1',
      tool_version_id: 'version-1',
      stable_name: 'mcp_fixture_lookup',
      version: '1.0.0',
      permissions: [],
      status: 'active',
      unavailable_reason: null,
      credential: null,
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <ExternalToolGrants agentId="agent-1" />
        </ToastProvider>
      </QueryClientProvider>
    )
    await waitFor(() => expect(screen.getByText(/mcp_fixture_lookup/)).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '授权' }))
    await waitFor(() =>
      expect(createExternalToolGrant).toHaveBeenCalledWith('agent-1', {
        installation_id: 'install-1',
        tool_version_id: 'version-1',
        permissions: [],
      })
    )
    expect(screen.queryByText(/credential value|ciphertext/i)).not.toBeInTheDocument()
  })
})
