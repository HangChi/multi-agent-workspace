/**
 * 外部工具授权（010+）：独立于内置工具白名单，授权固定到已安装版本。
 *
 * 三态分离：三个查询（授权 / 安装 / 服务端工具）各自有 loading / error /
 * ready。此前加载中或失败时显示「请先从工具市场安装已发布工具」——
 * 把故障伪装成引导文案。状态徽章做中文映射（原来直接渲染
 * active/revoked 英文枚举）。
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  createExternalToolGrant,
  fetchExternalServers,
  fetchExternalToolGrants,
  fetchMarketplaceInstallations,
  revokeExternalToolGrant,
} from '../../api/endpoints'
import { extractError } from '../../api/client'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Skeleton } from '../ui/Skeleton'
import { useToast } from '../ui/useToast'

const GRANT_STATUS_LABEL: Record<string, string> = {
  active: '已授权',
  revoked: '已撤销',
  expired: '已过期',
}

export function ExternalToolGrants({ agentId }: { agentId: string }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const grantsQuery = useQuery({
    queryKey: ['external-tool-grants', agentId],
    queryFn: () => fetchExternalToolGrants(agentId),
  })
  const installationsQuery = useQuery({
    queryKey: ['marketplace-installations'],
    queryFn: fetchMarketplaceInstallations,
  })
  const serversQuery = useQuery({
    queryKey: ['external-tool-servers'],
    queryFn: fetchExternalServers,
  })
  const grant = useMutation({
    mutationFn: ({ installationId, versionId }: { installationId: string; versionId: string }) =>
      createExternalToolGrant(agentId, {
        installation_id: installationId,
        tool_version_id: versionId,
        permissions: [],
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['external-tool-grants', agentId] })
      toast.success('已授权外部工具')
    },
    onError: (err) => toast.error('授权失败', extractError(err)),
  })
  const revoke = useMutation({
    mutationFn: (grantId: string) => revokeExternalToolGrant(agentId, grantId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['external-tool-grants', agentId] })
      toast.success('已撤销授权')
    },
    onError: (err) => toast.error('撤销失败', extractError(err)),
  })

  const installedListingIds = new Set(
    (installationsQuery.data?.items ?? [])
      .filter((item) => item.status === 'installed')
      .map((item) => item.listing_id)
  )
  const available = (serversQuery.data?.items ?? []).flatMap((server) =>
    server.tools
      .filter(
        (tool) =>
          tool.listing_id &&
          installedListingIds.has(tool.listing_id) &&
          tool.availability === 'valid'
      )
      .map((tool) => ({
        tool,
        installation: installationsQuery.data?.items.find(
          (item) => item.listing_id === tool.listing_id
        ),
      }))
  )

  const loading = grantsQuery.isLoading || installationsQuery.isLoading || serversQuery.isLoading
  const failed = grantsQuery.isError || installationsQuery.isError || serversQuery.isError
  const grants = grantsQuery.data?.items ?? []

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-ink">外部工具授权</h3>
          <p className="mt-1 text-xs text-ink-muted">
            独立于内置工具白名单；授权固定到已安装版本。
          </p>
        </div>
        <Badge tone="slate">{grantsQuery.data?.total ?? 0} 个</Badge>
      </div>

      {loading && (
        <div className="mt-3 space-y-2" aria-hidden="true">
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
        </div>
      )}

      {failed && (
        <div className="mt-3">
          <ErrorBanner
            error="外部工具数据加载失败"
            retry={() => {
              void grantsQuery.refetch()
              void installationsQuery.refetch()
              void serversQuery.refetch()
            }}
          />
        </div>
      )}

      {!loading && !failed && (
        <>
          <div className="mt-3 divide-y divide-line-subtle">
            {grants.map((item) => (
              <div key={item.id} className="flex flex-wrap items-center gap-2 py-2">
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">
                  {item.stable_name} · v{item.version}
                </span>
                <Badge
                  tone={item.status === 'active' && !item.unavailable_reason ? 'emerald' : 'amber'}
                >
                  {item.unavailable_reason ??
                    (GRANT_STATUS_LABEL[item.status] ?? item.status)}
                </Badge>
                {item.status !== 'revoked' && (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={revoke.isPending}
                    onClick={() => revoke.mutate(item.id)}
                  >
                    撤销
                  </Button>
                )}
              </div>
            ))}
            {available.map(
              ({ tool, installation }) =>
                installation &&
                !grants.some(
                  (grantItem) => grantItem.tool_version_id === tool.id && grantItem.status === 'active'
                ) && (
                  <div key={tool.id} className="flex flex-wrap items-center gap-2 py-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">
                      {tool.stable_name} · v{tool.version}
                    </span>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={grant.isPending}
                      onClick={() =>
                        grant.mutate({ installationId: installation.id, versionId: tool.id })
                      }
                    >
                      授权
                    </Button>
                  </div>
                )
            )}
          </div>
          {/* 真的没有可授权的对象时才给引导（原来加载中也显示这句） */}
          {available.length === 0 && grants.length === 0 && (
            <p className="mt-3 text-xs text-ink-faint">
              请先从工具市场安装已发布工具，安装后可在这里授权给该 Agent。
            </p>
          )}
        </>
      )}
    </Card>
  )
}
