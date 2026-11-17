/** Member-facing published external MCP tool marketplace. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  fetchMarketplaceInstallations,
  fetchMarketplaceTools,
  installMarketplaceTool,
} from '../api/endpoints'
import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Icon } from '../components/ui/Icons'
import { Input } from '../components/ui/Field'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { Spinner } from '../components/ui/Spinner'

export function MarketplacePage() {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [capability, setCapability] = useState('')
  const [applied, setApplied] = useState({ q: '', category: '', capability: '' })
  const queryClient = useQueryClient()
  const { data: installations } = useQuery({
    queryKey: ['marketplace-installations'],
    queryFn: fetchMarketplaceInstallations,
  })
  const install = useMutation({
    mutationFn: (listingId: string) => installMarketplaceTool(listingId, 'pinned'),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['marketplace-installations'] })
    },
  })
  const installedListingIds = new Set(
    (installations?.items ?? [])
      .filter((item) => item.status === 'installed')
      .map((item) => item.listing_id)
  )
  const { data, isLoading, isError } = useQuery({
    queryKey: ['marketplace-tools', applied],
    queryFn: () =>
      fetchMarketplaceTools({
        q: applied.q || undefined,
        category: applied.category || undefined,
        capability: applied.capability || undefined,
      }),
  })

  return (
    <Page>
      <PageHeader
        title="工具市场"
        description="浏览已审核、可安全安装的第三方 MCP 工具。"
        icon={
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-50 text-accent ring-1 ring-inset ring-brand-100">
            <Icon name="zap" className="h-5 w-5" />
          </span>
        }
      />
      <Card>
        <form
          className="grid gap-2 sm:grid-cols-[1fr_10rem_10rem_auto]"
          onSubmit={(event) => {
            event.preventDefault()
            setApplied({
              q: query.trim(),
              category: category.trim(),
              capability: capability.trim(),
            })
          }}
        >
          <Input
            aria-label="搜索工具市场"
            placeholder="搜索工具、服务器或描述"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            wrapperClassName="min-w-0"
            leftIcon="search"
          />
          <Input
            aria-label="按分类筛选"
            placeholder="分类"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            wrapperClassName="min-w-0"
          />
          <Input
            aria-label="按权限筛选"
            placeholder="权限"
            value={capability}
            onChange={(event) => setCapability(event.target.value)}
            wrapperClassName="min-w-0"
          />
          <button
            type="submit"
            className="h-10 rounded-lg bg-brand-600 px-4 text-sm font-medium text-white transition hover:bg-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/45"
          >
            筛选
          </button>
        </form>
      </Card>
      {isError && <ErrorBanner error="工具市场加载失败，请稍后重试。" />}
      {isLoading ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-5 w-5 text-brand-500" />
        </div>
      ) : data?.items.length === 0 ? (
        <EmptyState
          icon="search"
          title="没有匹配的工具"
          description="尝试更换关键词或清空筛选条件。"
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {data?.items.map((listing) => (
            <Card key={listing.listing_id} className="flex flex-col gap-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-base font-semibold text-ink">
                    {listing.display_name}
                  </h2>
                  <p className="mt-1 text-xs text-ink-muted">
                    {listing.server_name} · v{listing.version}
                  </p>
                </div>
                <Badge tone="emerald" dot>
                  已发布
                </Badge>
              </div>
              <p className="min-h-10 text-sm leading-relaxed text-ink-soft">
                {listing.description}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {listing.permissions.map((permission) => (
                  <Badge key={permission} tone="sky">
                    {permission}
                  </Badge>
                ))}
              </div>
              <div className="mt-auto flex items-center justify-between border-t border-line-subtle pt-3 text-xs text-ink-faint">
                <span className="font-mono">{listing.stable_name}</span>
                <button
                  type="button"
                  disabled={installedListingIds.has(listing.listing_id) || install.isPending}
                  onClick={() => install.mutate(listing.listing_id)}
                  className="rounded-lg border border-line px-3 py-1.5 font-medium text-ink transition hover:bg-surface-subtle disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {installedListingIds.has(listing.listing_id)
                    ? '已安装'
                    : install.isPending && install.variables === listing.listing_id
                      ? '安装中…'
                      : '安装'}
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </Page>
  )
}
