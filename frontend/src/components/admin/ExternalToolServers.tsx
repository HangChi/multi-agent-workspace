/** Admin review controls for discovered external MCP tool versions. */

import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import {
  approveExternalTool,
  fetchExternalServers,
  pauseMarketplaceTool,
  rejectExternalTool,
  revokeMarketplaceTool,
} from '../../api/endpoints'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { ErrorBanner } from '../ui/ErrorBanner'

export function ExternalToolServers() {
  const queryClient = useQueryClient()
  const [reason, setReason] = useState('Reviewed source and data handling')
  const [error, setError] = useState<string | null>(null)
  const { data, isLoading } = useQuery({ queryKey: ['external-tool-servers'], queryFn: fetchExternalServers })
  const review = useMutation({
    mutationFn: ({ serverId, versionId, approve }: { serverId: string; versionId: string; approve: boolean }) =>
      (approve ? approveExternalTool(serverId, versionId, { reason }) : rejectExternalTool(serverId, versionId, { reason })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['external-tool-servers'] }),
    onError: (cause) => setError(cause instanceof Error ? cause.message : '审核操作失败'),
  })
  const lifecycle = useMutation({
    mutationFn: ({ listingId, revoke }: { listingId: string; revoke: boolean }) =>
      (revoke ? revokeMarketplaceTool(listingId, { reason }) : pauseMarketplaceTool(listingId, { reason })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['external-tool-servers'] }),
    onError: (cause) => setError(cause instanceof Error ? cause.message : '状态变更失败'),
  })

  if (isLoading) return <Card><p className="text-sm text-ink-muted">正在加载外部工具服务器…</p></Card>
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 className="text-sm font-semibold text-ink">外部工具审核</h2><p className="mt-1 text-xs text-ink-muted">仅发布验证通过的工具版本。凭证值不会显示。</p></div>
        <Badge tone="slate">{data?.total ?? 0} 个服务器</Badge>
      </div>
      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}
      <label className="mt-4 block text-xs font-medium text-ink-muted">审核说明
        <input value={reason} onChange={(event) => setReason(event.target.value)} className="mt-1 h-9 w-full rounded-lg border border-line bg-surface px-3 text-sm text-ink outline-none focus:border-brand-500 sm:max-w-md" />
      </label>
      <div className="mt-4 divide-y divide-line-subtle">
        {(data?.items ?? []).flatMap((server) => server.tools.map((tool) => ({ server, tool }))).map(({ server, tool }) => (
          <div key={tool.id} className="flex flex-wrap items-center gap-3 py-3">
            <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-ink">{server.name} / {tool.external_name}</p><p className="truncate text-xs text-ink-muted">v{tool.version} · {tool.description}</p></div>
            <Badge tone={server.current_validation_status === 'passed' ? 'emerald' : 'amber'}>{server.current_validation_status ?? '待验证'}</Badge>
            <div className="flex flex-wrap gap-2">
              {tool.listing_status === 'published' && tool.listing_id ? (
                <>
                  <Button size="sm" variant="secondary" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ listingId: tool.listing_id!, revoke: false })}>暂停</Button>
                  <Button size="sm" variant="secondary" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ listingId: tool.listing_id!, revoke: true })}>撤销</Button>
                </>
              ) : (
                <>
                  <Button size="sm" variant="primary" disabled={review.isPending || server.current_validation_status !== 'passed'} onClick={() => review.mutate({ serverId: server.id, versionId: tool.id, approve: true })}>发布</Button>
                  <Button size="sm" variant="secondary" disabled={review.isPending} onClick={() => review.mutate({ serverId: server.id, versionId: tool.id, approve: false })}>拒绝</Button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}
