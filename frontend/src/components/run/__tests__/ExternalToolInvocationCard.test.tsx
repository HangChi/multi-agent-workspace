import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ExternalToolInvocationCard } from '../ExternalToolInvocationCard'

describe('ExternalToolInvocationCard', () => {
  it('shows only safe tool identity, version, duration, and error category', () => {
    render(
      <ExternalToolInvocationCard
        invocation={{
          id: 'invocation-1',
          step_id: 'step-1',
          server_name: 'Reviewed Search',
          tool_name: 'search',
          stable_name: 'mcp_reviewed_search_a1b2',
          version: '1.2.0',
          status: 'auth_error',
          duration_ms: 84,
          error_category: 'auth_error',
          result_truncated: false,
          created_at: '2026-11-15T12:00:00Z',
        }}
      />
    )

    expect(screen.getByText(/Reviewed Search \/ search/)).toBeInTheDocument()
    expect(screen.getByText(/1\.2\.0/)).toBeInTheDocument()
    expect(screen.getByText('84 ms')).toBeInTheDocument()
    expect(screen.getByText('auth_error')).toBeInTheDocument()
    expect(screen.queryByText(/credential|ciphertext|raw output/i)).not.toBeInTheDocument()
  })
})
