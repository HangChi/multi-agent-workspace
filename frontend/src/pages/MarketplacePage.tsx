/**
 * 工具市场（003）：面向成员的可安装 MCP 工具列表。
 *
 * 细节：
 * - 状态徽章由 listing.status 派生（此前硬编码「已发布」，paused/revoked
 *   也会显示成已发布——徽章撒谎比没有徽章更糟）；
 * - 安装状态查询失败时降级显示「安装状态未知」，而不是把未安装当已安装；
 * - 安装动作有明确成败反馈（此前完全静默）；
 * - 卡片信息对齐主流市场（VS Code Extensions / npm）：名称 + 版本 +
 *   发布者（服务器）+ 权限标签 + 稳定名；宽屏三列。
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  fetchMarketplaceInstallations,
  fetchMarketplaceTools,
  installMarketplaceTool,
} from '../api/endpoints'
import type { MarketplaceListing } from '../api/endpoints'
import { extractError } from '../api/client'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Icon } from '../components/ui/Icons'
import { Input } from '../components/ui/Field'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { CardSkeleton } from '../components/ui/Skeleton'
import { useToast } from '../components/ui/useToast'

const STATUS_META: Record<
  MarketplaceListing['status'],
  { label: string; tone: 'emerald' | 'amber' | 'rose' | 'slate' }
> = {
  published: { label: '已发布', tone: 'emerald' },
  pending_review: { label: '审核中', tone: 'amber' },
  paused: { label: '已暂停', tone: 'slate' },
  rejected: { label: '未通过', tone: 'rose' },
  revoked: { label: '已下架', tone: 'rose' },
}

export function MarketplacePage() {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [capability, setCapability] = useState('')
  const [applied, setApplied] = useState({ q: '', category: '', capability: '' })
  const queryClient = useQueryClient()
  const toast = useToast()
  /** 是否处于筛选状态：决定空态文案与「清除」按钮是否出现 */
  const hasFilters = applied.q !== '' || applied.category !== '' || applied.capability !== ''

  const { data: installations, isError: installationsError } = useQuery({
    queryKey: ['marketplace-installations'],
    queryFn: fetchMarketplaceInstallations,
  })
  const install = useMutation({
    mutationFn: (listingId: string) => installMarketplaceTool(listingId, 'pinned'),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['marketplace-installations'] })
      toast.success('工具已安装', '安装后可到 Agent 编辑页授权给具体 Agent')
    },
    onError: (err) => toast.error('安装失败', extractError(err)),
  })

  // 安装状态未知（查询失败）时不能把「未安装」当结论——按钮会误导用户
  const installedListingIds = installationsError
    ? null
    : new Set(
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

  const clearFilters = () => {
    setQuery('')
    setCategory('')
    setCapability('')
    setApplied({ q: '', category: '', capability: '' })
  }

  const items = data?.items ?? []

  return (
    <Page>
      <PageHeader
        eyebrow="设置"
        title="工具市场"
        description="浏览已审核、可安全安装的第三方 MCP 工具。"
        icon={<Icon name="plug" />}
        actions={
          items.length > 0 && (
            <p className="text-micro text-ink-faint">
              共 <span className="font-medium text-ink-soft">{data?.total ?? items.length}</span> 个工具
            </p>
          )
        }
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
            <Button type="submit" variant="primary" leftIcon="search" loading={isLoading}>
              筛选
            </Button>
            {hasFilters && (
              <Button type="button" variant="ghost" leftIcon="x" onClick={clearFilters}>
                清除
              </Button>
            )}
          </div>
        </form>
      </Card>
      {isError && <ErrorBanner error="工具市场加载失败，请稍后重试。" />}
      {installationsError && (
        <ErrorBanner error="安装状态加载失败，工具可能显示为未安装——请以 Agent 编辑页的授权列表为准" />
      )}
      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : items.length === 0 ? (
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
              <Button variant="secondary" leftIcon="refresh" onClick={clearFilters}>
                清除筛选条件
              </Button>
            ) : (
              // Button as=link：跳转型操作用 Link 语义，不再 <a><button> 嵌套
              <Button as="link" to="/settings/admin" variant="secondary" leftIcon="shield">
                前往管理后台
              </Button>
            )
          }
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((listing) => {
            const status = STATUS_META[listing.status] ?? {
              label: listing.status,
              tone: 'slate' as const,
            }
            const installed = installedListingIds?.has(listing.listing_id) ?? false
            return (
              <Card key={listing.listing_id} className="flex flex-col gap-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate text-body font-semibold text-ink">
                      {listing.display_name}
                    </h2>
                    <p className="mt-1 text-xs text-ink-muted">
                      {listing.server_name} · v{listing.version}
                      {listing.validated_at
                        ? ` · 验证于 ${new Date(listing.validated_at).toLocaleDateString('zh-CN')}`
                        : ''}
                    </p>
                  </div>
                  <Badge tone={status.tone} dot>
                    {status.label}
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
                  <Button
                    size="sm"
                    variant={installed ? 'secondary' : 'primary'}
                    // 只禁当前卡片：此前任一安装中禁用全部卡片
                    disabled={installed || (install.isPending && install.variables === listing.listing_id)}
                    loading={install.isPending && install.variables === listing.listing_id}
                    onClick={() => install.mutate(listing.listing_id)}
                  >
                    {installed
                      ? '已安装'
                      : install.isPending && install.variables === listing.listing_id
                        ? '安装中…'
                        : '安装'}
                  </Button>
                </div>
              </Card>
            )
          })}
        </div>
      )}
    </Page>
  )
}
