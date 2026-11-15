/** Minimal feature-003 API fixtures for component tests. */

export const marketplaceToolFixture = {
  listing_id: 'listing-1',
  tool_version_id: 'version-1',
  stable_name: 'mcp_fixture_search',
  display_name: 'Fixture MCP / search',
  description: 'Search fixture data',
  server_name: 'Fixture MCP',
  version: '1.0.0',
  permissions: ['fixture.read'],
  restrictions: { timeout_seconds: 30, max_result_bytes: 65536 },
  status: 'published' as const,
  validated_at: '2026-11-15T00:00:00Z',
}

export const externalInvocationFixture = {
  id: 'invocation-1',
  step_id: 'step-1',
  server_name: 'Fixture MCP',
  tool_name: 'search',
  stable_name: 'mcp_fixture_search',
  version: '1.0.0',
  status: 'succeeded' as const,
  duration_ms: 42,
  error_category: null,
  result_truncated: false,
}
