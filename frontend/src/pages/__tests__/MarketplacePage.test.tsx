import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import { fetchMarketplaceInstallations, fetchMarketplaceTools, installMarketplaceTool } from '../../api/endpoints'
import { marketplaceToolFixture } from '../../test/mcpHandlers'
import { MarketplacePage } from '../MarketplacePage'

vi.mock('../../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/endpoints')>()),
  fetchMarketplaceTools: vi.fn(),
  fetchMarketplaceInstallations: vi.fn(),
  installMarketplaceTool: vi.fn(),
}))

describe('MarketplacePage', () => {
  it('shows only safe published listing metadata and supports empty state', async () => {
    vi.mocked(fetchMarketplaceTools).mockResolvedValue({ items: [marketplaceToolFixture], total: 1 })
    vi.mocked(fetchMarketplaceInstallations).mockResolvedValue({ items: [], total: 0 })
    vi.mocked(installMarketplaceTool).mockResolvedValue({ id: 'install-1', listing_id: marketplaceToolFixture.listing_id, owner_id: 'owner-1', status: 'installed', version_policy: 'pinned', installed_at: 'now' })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MarketplacePage /></QueryClientProvider>)
    await waitFor(() => expect(screen.getByText('Fixture MCP / search')).toBeInTheDocument())
    expect(screen.getByText('mcp_fixture_search')).toBeInTheDocument()
    expect(screen.queryByText(/credential|authorization|endpoint/i)).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '按分类筛选' }), { target: { value: 'search' } })
    fireEvent.change(screen.getByRole('textbox', { name: '按权限筛选' }), { target: { value: 'fixture.read' } })
    fireEvent.click(screen.getByRole('button', { name: '筛选' }))
    await waitFor(() => expect(fetchMarketplaceTools).toHaveBeenLastCalledWith({ category: 'search', capability: 'fixture.read', q: undefined }))
    const installButton = await screen.findByRole('button', { name: '安装' })
    fireEvent.click(installButton)
    await waitFor(() => expect(installMarketplaceTool).toHaveBeenCalledWith(marketplaceToolFixture.listing_id, 'pinned'))
  })
})
