import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

import {
  fetchExternalServers,
  pauseMarketplaceTool,
  revokeExternalCredential,
  resumeMarketplaceTool,
  rotateExternalCredential,
} from '../api/endpoints'
import { ExternalToolServers } from '../components/admin/ExternalToolServers'
import { ConfirmProvider } from '../components/ui/ConfirmDialog'
import { ToastProvider } from '../components/ui/Toast'

vi.mock('../api/endpoints', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/endpoints')>()),
  fetchExternalServers: vi.fn(),
  pauseMarketplaceTool: vi.fn(),
  resumeMarketplaceTool: vi.fn(),
  revokeExternalCredential: vi.fn(),
  rotateExternalCredential: vi.fn(),
}))

describe('external lifecycle controls', () => {
  it('shows credential metadata only and supports pause/resume and revoke', async () => {
    vi.mocked(fetchExternalServers).mockResolvedValue({
      total: 1,
      items: [
        {
          id: 'server-1',
          name: 'Reviewed Server',
          endpoint_url: 'https://example.test/mcp',
          maintainer: 'Platform',
          status: 'published',
          current_validation_status: 'passed',
          credentials: [
            {
              id: 'credential-1',
              label: 'production',
              kind: 'bearer',
              status: 'active',
              key_version: 'v1',
            },
          ],
          tools: [
            {
              id: 'version-1',
              external_name: 'search',
              stable_name: 'mcp_reviewed_search_v1',
              version: '1.0.0',
              description: 'Reviewed search',
              required_permissions: ['records.read'],
              availability: 'valid',
              listing_id: 'listing-1',
              listing_status: 'published',
            },
          ],
        },
      ],
    })
    vi.mocked(pauseMarketplaceTool).mockResolvedValue({} as never)
    vi.mocked(resumeMarketplaceTool).mockResolvedValue({} as never)
    vi.mocked(revokeExternalCredential).mockResolvedValue()
    vi.mocked(rotateExternalCredential).mockResolvedValue({
      id: 'credential-2',
      label: 'rotated',
      kind: 'bearer',
      status: 'active',
      key_version: 'v1',
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <ConfirmProvider>
            <ExternalToolServers />
          </ConfirmProvider>
        </ToastProvider>
      </QueryClientProvider>
    )

    await waitFor(() => expect(screen.getByText('Reviewed Server / search')).toBeInTheDocument())
    expect(screen.getByText('production · active')).toBeInTheDocument()
    expect(screen.queryByText(/secret|ciphertext|token/i)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '暂停' }))
    await waitFor(() =>
      expect(pauseMarketplaceTool).toHaveBeenCalledWith('listing-1', { reason: expect.any(String) })
    )
    // 撤销凭证走确认框：点「撤销凭证」→ 确认框里点「撤销」才真正执行
    // （行内也有个「撤销」按钮，必须限定在对话框内找，否则匹配到两个）
    fireEvent.click(screen.getByRole('button', { name: '撤销凭证' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '撤销' }))
    await waitFor(() =>
      expect(revokeExternalCredential).toHaveBeenCalledWith('server-1', 'credential-1')
    )
  })
})
