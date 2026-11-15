/** External tools are managed separately from the builtin Agent tool allowlist. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  createExternalToolGrant,
  fetchExternalServers,
  fetchExternalToolGrants,
  fetchMarketplaceInstallations,
  revokeExternalToolGrant,
} from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'

export function ExternalToolGrants({ agentId }: { agentId: string }) {
  const queryClient = useQueryClient()
  const grantsQuery = useQuery({ queryKey: ['external-tool-grants', agentId], queryFn: () => fetchExternalToolGrants(agentId) })
  const installationsQuery = useQuery({ queryKey: ['marketplace-installations'], queryFn: fetchMarketplaceInstallations })
  const serversQuery = useQuery({ queryKey: ['external-tool-servers'], queryFn: fetchExternalServers })
  const grant = useMutation({
    mutationFn: ({ installationId, versionId }: { installationId: string; versionId: string }) =>
      createExternalToolGrant(agentId, { installation_id: installationId, tool_version_id: versionId, permissions: [] }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['external-tool-grants', agentId] })
    },
  })
  const revoke = useMutation({
    mutationFn: (grantId: string) => revokeExternalToolGrant(agentId, grantId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['external-tool-grants', agentId] })
    },
  })

  const installedListingIds = new Set((installationsQuery.data?.items ?? []).filter((item) => item.status === 'installed').map((item) => item.listing_id))
  const available = (serversQuery.data?.items ?? []).flatMap((server) => server.tools
    .filter((tool) => tool.listing_id && installedListingIds.has(tool.listing_id) && tool.availability === 'valid')
    .map((tool) => ({ tool, installation: installationsQuery.data?.items.find((item) => item.listing_id === tool.listing_id) })))

  return (
    <Card>
      <div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-ink">外部工具授权</h3><p className="mt-1 text-xs text-ink-muted">独立于内置工具白名单；授权固定到已安装版本。</p></div><Badge tone="slate">{grantsQuery.data?.total ?? 0} 个</Badge></div>
      <div className="mt-3 divide-y divide-line-subtle">
        {(grantsQuery.data?.items ?? []).map((item) => <div key={item.id} className="flex flex-wrap items-center gap-2 py-2"><span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{item.stable_name} · v{item.version}</span><Badge tone={item.status === 'active' && !item.unavailable_reason ? 'emerald' : 'amber'}>{item.unavailable_reason ?? item.status}</Badge>{item.status !== 'revoked' && <Button size="sm" variant="secondary" disabled={revoke.isPending} onClick={() => revoke.mutate(item.id)}>撤销</Button>}</div>)}
        {available.map(({ tool, installation }) => installation && !(grantsQuery.data?.items ?? []).some((grantItem) => grantItem.tool_version_id === tool.id && grantItem.status === 'active') && <div key={tool.id} className="flex flex-wrap items-center gap-2 py-2"><span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{tool.stable_name} · v{tool.version}</span><Button size="sm" variant="primary" disabled={grant.isPending} onClick={() => grant.mutate({ installationId: installation.id, versionId: tool.id })}>授权</Button></div>)}
      </div>
      {!available.length && !grantsQuery.data?.items.length && <p className="mt-3 text-xs text-ink-faint">请先从工具市场安装已发布工具。</p>}
    </Card>
  )
}
