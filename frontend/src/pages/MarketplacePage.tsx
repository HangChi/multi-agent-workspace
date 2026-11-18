/** Member-facing published external MCP tool marketplace. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import {
  fetchMarketplaceInstallations,
  fetchMarketplaceTools,
  installMarketplaceTool,
} from '../api/endpoints'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
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
  /** 是否处于筛选状态：决定空态文案与「清除」按钮是否出现 */
  const hasFilters = applied.q !== '' || applied.category !== '' || applied.capability !== ''
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
        eyebrow="设置"
        title="工具市场"
        description="浏览已审核、可安全安装的第三方 MCP 工具。"
        icon={<Icon name="plug" />}
      />
      <Card padding="md">
        <form
          className="grid gap-2 sm:grid-cols-[1fr_9rem_9rem_auto]"
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
          <div className="flex items-center gap-2">
            <Button
              type="submit"
              variant="primary"
              leftIcon="search"
              loading={isLoading}
              className="h-10"
            >
              筛选
            </Button>
            {hasFilters && (
              <Button
                type="button"
                variant="ghost"
                leftIcon="x"
                onClick={() => {
                  setQuery('')
                  setCategory('')
                  setCapability('')
                  setApplied({ q: '', category: '', capability: '' })
                }}
              >
                清除
              </Button>
            )}
          </div>
        </form>
      </Card>
      {isError && <ErrorBanner error="工具市场加载失败，请稍后重试。" />}
      {isLoading ? (
        <div className="flex justify-center py-16">
          <Spinner className="h-5 w-5 text-brand-500" />
        </div>
      ) : data?.items.length === 0 ? (
        <EmptyState
          icon={hasFilters ? 'search' : 'plug'}
          title={hasFilters ? '没有匹配的工具' : '市场上还没有发布的工具'}
          description={
            hasFilters
              ? '尝试更换关键词，或清除筛选条件看看全部工具。'
              : '管理员可以在「管理后台 → 外部工具服务」里发布可安装的 MCP 工具。'
          }
          action={
            hasFilters ? (
              <Button
                variant="secondary"
                leftIcon="refresh"
                onClick={() => {
                  setQuery('')
                  setCategory('')
                  setCapability('')
                  setApplied({ q: '', category: '', capability: '' })
                }}
              >
                清除筛选条件
              </Button>
            ) : (
              <Link to="/settings/admin">
                <Button variant="secondary" leftIcon="shield">
                  前往管理后台
                </Button>
              </Link>
            )
          }
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
