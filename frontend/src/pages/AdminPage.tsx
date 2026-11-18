/** 管理后台（T026 权限占位 + T079 审计列表）：仅 admin 可访问。 */

import { useEffect, useState } from 'react'

import {
  type AuditLogItem,
  type BlacklistRule,
  type EgressAccess,
  type ExternalInvocationAudit,
  createBlacklist,
  deleteBlacklist,
  fetchAuditLogs,
  fetchBlacklist,
  fetchEgressAccesses,
  fetchExternalInvocationAudit,
} from '../api/endpoints'
import { ExternalToolServers } from '../components/admin/ExternalToolServers'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, IconTile } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { Input } from '../components/ui/Field'
import { Icon } from '../components/ui/Icons'
import { Page } from '../components/ui/Page'
import { PageHeader } from '../components/ui/PageHeader'
import { Skeleton } from '../components/ui/Skeleton'
import { cn } from '../components/ui/cn'
import { formatBytes } from '../utils/formatBytes'

const PAGE_SIZE = 20

/** 时间格式化：YYYY-MM-DD HH:mm:ss（本地时区）。 */
function formatTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

/** 动作前缀决定标签配色，扫读时更容易区分模块。 */
function actionTone(action: string): 'brand' | 'emerald' | 'amber' | 'rose' | 'slate' | 'sky' {
  if (action.startsWith('user_') || action.startsWith('auth')) return 'brand'
  if (action.startsWith('agent')) return 'sky'
  if (action.startsWith('group')) return 'amber'
  if (action.includes('delete') || action.includes('fail')) return 'rose'
  if (action.includes('run')) return 'emerald'
  return 'slate'
}

export function AdminPage() {
  const [items, setItems] = useState<AuditLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  // 草稿（输入中）与已应用条件分离：避免每敲一个字符就查一次
  const [draftUser, setDraftUser] = useState('')
  const [draftAction, setDraftAction] = useState('')
  const [userFilter, setUserFilter] = useState('')
  const [actionFilter, setActionFilter] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [blacklist, setBlacklist] = useState<BlacklistRule[]>([])
  const [accesses, setAccesses] = useState<EgressAccess[]>([])
  const [externalInvocations, setExternalInvocations] = useState<ExternalInvocationAudit[]>([])
  const [externalInvocationTotal, setExternalInvocationTotal] = useState(0)
  const [invocationServerFilter, setInvocationServerFilter] = useState('')
  const [invocationStatusFilter, setInvocationStatusFilter] = useState('')
  const [invocationError, setInvocationError] = useState<string | null>(null)
  const [accessSummary, setAccessSummary] = useState<Record<string, number>>({})
  const [egressTarget, setEgressTarget] = useState('')
  const [egressBusy, setEgressBusy] = useState(false)
  const [egressError, setEgressError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchAuditLogs({
      user_id: userFilter || undefined,
      action: actionFilter || undefined,
      page,
      page_size: PAGE_SIZE,
    })
      .then((data) => {
        if (cancelled) return
        setItems(data.items)
        setTotal(data.total)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : '加载审计日志失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [page, actionFilter, userFilter, reloadKey])

  const loadEgress = async () => {
    setEgressError(null)
    try {
      const [blacklistData, accessData] = await Promise.all([
        fetchBlacklist(),
        fetchEgressAccesses({ page: 1, page_size: 50 }),
      ])
      setBlacklist(blacklistData.items)
      setAccesses(accessData.items)
      setAccessSummary(accessData.summary ?? {})
    } catch (e: unknown) {
      setEgressError(e instanceof Error ? e.message : '加载出站配置失败')
    }
  }

  useEffect(() => {
    let cancelled = false
    setInvocationError(null)
    fetchExternalInvocationAudit({
      server_id: invocationServerFilter || undefined,
      status: invocationStatusFilter || undefined,
      page: 1,
      page_size: 20,
    })
      .then((data) => {
        if (cancelled) return
        setExternalInvocations(data.items)
        setExternalInvocationTotal(data.total)
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setInvocationError(cause instanceof Error ? cause.message : '加载外部调用审计失败')
      })
    return () => {
      cancelled = true
    }
  }, [invocationServerFilter, invocationStatusFilter, reloadKey])

  const addBlacklist = async () => {
    const target = egressTarget.trim().toLowerCase()
    if (!target) return
    setEgressBusy(true)
    setEgressError(null)
    try {
      await createBlacklist({
        target_type: target.includes('/') ? 'cidr' : 'domain',
        target,
        port_range: null,
      })
      setEgressTarget('')
      await loadEgress()
    } catch (e: unknown) {
      setEgressError(e instanceof Error ? e.message : '黑名单保存失败')
    } finally {
      setEgressBusy(false)
    }
  }

  const removeBlacklist = async (rule: BlacklistRule) => {
    if (rule.builtin) return
    setEgressBusy(true)
    try {
      await deleteBlacklist(rule.id)
      await loadEgress()
    } catch {
      setEgressError('黑名单删除失败')
    } finally {
      setEgressBusy(false)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const hasFilter = Boolean(userFilter || actionFilter)

  /** 应用筛选：重置到第一页。 */
  function applyFilter() {
    setUserFilter(draftUser.trim())
    setActionFilter(draftAction.trim())
    setPage(1)
    setReloadKey((k) => k + 1)
  }

  function clearFilter() {
    setDraftUser('')
    setDraftAction('')
    setUserFilter('')
    setActionFilter('')
    setPage(1)
  }

  return (
    <Page>
      <PageHeader
        eyebrow="设置"
        title="管理后台"
        description="查看全站操作审计日志，支持按用户与动作筛选。"
        icon={
          <IconTile tone="slate" size="lg">
            <Icon name="shield" className="h-5 w-5" />
          </IconTile>
        }
        actions={
          <Button
            variant="secondary"
            leftIcon="refresh"
            loading={loading}
            onClick={() => setReloadKey((k) => k + 1)}
          >
            刷新
          </Button>
        }
      />

      {/* 筛选栏 */}
      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Input
            label="用户 ID"
            value={draftUser}
            onChange={(e) => setDraftUser(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') applyFilter()
            }}
            placeholder="按用户 ID 精确筛选"
            leftIcon="user"
            wrapperClassName="w-full sm:w-56"
            aria-label="按用户 ID 筛选"
          />
          <Input
            label="动作"
            value={draftAction}
            onChange={(e) => setDraftAction(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') applyFilter()
            }}
            placeholder="如 user_login / agent.create"
            leftIcon="filter"
            wrapperClassName="w-full sm:w-64"
            aria-label="按动作筛选"
          />
          <Button variant="primary" leftIcon="search" onClick={applyFilter}>
            查询
          </Button>
          <div className="flex items-center gap-2 pb-0.5">
            <Button
              variant="secondary"
              leftIcon="x"
              onClick={clearFilter}
              disabled={!hasFilter && !draftUser && !draftAction}
            >
              清空
            </Button>
            {hasFilter && (
              <span className="flex items-center gap-1 text-xs text-ink-faint">
                <Icon name="filter" className="h-3.5 w-3.5" />
                已筛选
              </span>
            )}
          </div>
          <div className="ml-auto flex items-center gap-1.5 pb-1 text-xs text-ink-faint">
            共<span className="font-semibold tabular-nums text-ink-soft">{total}</span>
            条记录
          </div>
        </div>
      </Card>

      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} />}

      {/* 审计列表 */}
      <Card padding="none" className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-line bg-surface-muted/80">
                <th className="w-[152px] whitespace-nowrap px-4 py-2.5 text-left text-micro font-semibold uppercase tracking-wide text-ink-muted">
                  时间
                </th>
                <th className="w-[118px] whitespace-nowrap px-4 py-2.5 text-left text-micro font-semibold uppercase tracking-wide text-ink-muted">
                  用户
                </th>
                <th className="w-[150px] whitespace-nowrap px-4 py-2.5 text-left text-micro font-semibold uppercase tracking-wide text-ink-muted">
                  动作
                </th>
                <th className="w-[172px] whitespace-nowrap px-4 py-2.5 text-left text-micro font-semibold uppercase tracking-wide text-ink-muted">
                  目标
                </th>
                <th className="px-4 py-2.5 text-left text-micro font-semibold uppercase tracking-wide text-ink-muted">
                  详情
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-subtle">
              {loading && items.length === 0 ? (
                Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i}>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-32" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-16" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-5 w-24 rounded" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-20" />
                    </td>
                    <td className="px-4 py-3">
                      <Skeleton className="h-3.5 w-40" />
                    </td>
                  </tr>
                ))
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={5}>
                    <EmptyState
                      bordered={false}
                      icon="inbox"
                      title={hasFilter ? '没有匹配的审计记录' : '暂无审计日志'}
                      description={
                        hasFilter
                          ? '试试放宽筛选条件：用户 ID 需完整匹配，动作可用前缀，如 agent。'
                          : '系统会在用户登录、创建 Agent、分配需求等操作后自动记录。'
                      }
                      action={
                        hasFilter ? (
                          <Button variant="secondary" leftIcon="x" onClick={clearFilter}>
                            清空筛选
                          </Button>
                        ) : undefined
                      }
                    />
                  </td>
                </tr>
              ) : (
                items.map((item) => (
                  <tr
                    key={item.id}
                    className={cn(
                      'transition-colors hover:bg-surface-muted/80',
                      loading && 'opacity-60'
                    )}
                  >
                    <td className="whitespace-nowrap px-4 py-2.5 text-xs tabular-nums text-ink-muted">
                      {formatTime(item.created_at)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5">
                      <span className="rounded bg-surface-subtle px-1.5 py-0.5 font-mono text-micro text-ink-soft">
                        {item.user_id.slice(0, 8)}…
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5">
                      <Badge tone={actionTone(item.action)}>{item.action}</Badge>
                    </td>
                    <td className="whitespace-nowrap px-4 py-2.5 text-xs text-ink-muted">
                      {item.target_type ? (
                        <span>
                          {item.target_type}
                          {item.target_id && (
                            <span className="ml-1 font-mono text-micro text-ink-faint">
                              {item.target_id.slice(0, 8)}…
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="text-ink-faint">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className="block truncate font-mono text-micro text-ink-muted"
                        title={item.detail ? JSON.stringify(item.detail) : undefined}
                      >
                        {item.detail ? JSON.stringify(item.detail) : '—'}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* 分页 */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-subtle px-4 py-3">
          <p className="text-xs text-ink-muted">
            第 <span className="font-medium text-ink-soft">{page}</span> / {totalPages} 页
            <span className="mx-1.5 text-ink-faint">·</span>
            每页 {PAGE_SIZE} 条
          </p>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              leftIcon="chevron-left"
              disabled={page <= 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              上一页
            </Button>
            <Button
              size="sm"
              variant="secondary"
              rightIcon="chevron-right"
              disabled={page >= totalPages || loading}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              下一页
            </Button>
          </div>
        </div>
      </Card>

      <ExternalToolServers />

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-ink">外部工具调用审计</h2>
            <p className="mt-1 text-xs text-ink-muted">
              显示服务器、版本、运行、结果分类与策略事实；不包含原始参数、结果或凭证。
            </p>
          </div>
          <Input
            label="服务器 ID"
            aria-label="外部审计服务器筛选"
            value={invocationServerFilter}
            onChange={(event) => setInvocationServerFilter(event.target.value)}
            wrapperClassName="w-full sm:w-64"
          />
          <Input
            label="状态"
            aria-label="外部审计状态筛选"
            value={invocationStatusFilter}
            onChange={(event) => setInvocationStatusFilter(event.target.value)}
            placeholder="auth_error"
            wrapperClassName="w-full sm:w-40"
          />
          <Badge tone="slate">共 {externalInvocationTotal} 条</Badge>
        </div>
        {invocationError && (
          <ErrorBanner error={invocationError} onDismiss={() => setInvocationError(null)} />
        )}
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead>
              <tr className="border-b border-line text-left text-micro uppercase tracking-wide text-ink-muted">
                <th className="px-2 py-2">时间</th>
                <th className="px-2 py-2">运行</th>
                <th className="px-2 py-2">服务器 / 版本</th>
                <th className="px-2 py-2">状态</th>
                <th className="px-2 py-2">耗时</th>
                <th className="px-2 py-2">策略</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line-subtle">
              {externalInvocations.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-2 py-5 text-center text-ink-faint">
                    暂无外部工具调用记录。
                  </td>
                </tr>
              ) : (
                externalInvocations.map((item) => (
                  <tr key={item.id}>
                    <td className="whitespace-nowrap px-2 py-2 text-ink-muted">
                      {formatTime(item.created_at)}
                    </td>
                    <td className="px-2 py-2 font-mono text-ink-muted">
                      {item.run_id.slice(0, 8)}…
                    </td>
                    <td className="px-2 py-2 font-mono text-ink-muted">
                      {item.server_id.slice(0, 8)}… · {item.tool_version_id.slice(0, 8)}…
                    </td>
                    <td className="px-2 py-2">
                      <Badge
                        tone={
                          item.status === 'succeeded' || item.status === 'result_truncated'
                            ? 'emerald'
                            : 'amber'
                        }
                      >
                        {item.status}
                      </Badge>
                    </td>
                    <td className="px-2 py-2 tabular-nums text-ink-muted">{item.duration_ms} ms</td>
                    <td className="max-w-48 truncate px-2 py-2 font-mono text-ink-faint">
                      {JSON.stringify(item.policy_result)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-ink">全局出站黑名单</h2>
              <p className="mt-1 text-xs text-ink-muted">
                内置内网与云元数据规则不可删除，自定义规则会覆盖所有 Agent。
              </p>
            </div>
            <Button
              size="sm"
              variant="secondary"
              loading={egressBusy}
              onClick={() => void loadEgress()}
            >
              刷新
            </Button>
          </div>
          <div className="mt-3 flex gap-2">
            <Input
              aria-label="黑名单目标"
              value={egressTarget}
              onChange={(e) => setEgressTarget(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void addBlacklist()
              }}
              placeholder="例如 10.0.0.0/8 或 blocked.example.com"
              wrapperClassName="min-w-0 flex-1"
            />
            <Button
              size="sm"
              variant="primary"
              loading={egressBusy}
              onClick={() => void addBlacklist()}
            >
              添加
            </Button>
          </div>
          {egressError && (
            <p role="alert" className="mt-2 text-xs text-rose-600">
              {egressError}
            </p>
          )}
          <ul className="mt-3 divide-y divide-line rounded-lg border border-line">
            {blacklist.length === 0 ? (
              <li className="px-3 py-4 text-xs text-ink-faint">暂无黑名单规则。</li>
            ) : (
              blacklist.map((rule) => (
                <li key={rule.id} className="flex items-center gap-2 px-3 py-2 text-xs">
                  <span className="min-w-0 flex-1 truncate font-mono text-ink" title={rule.target}>
                    {rule.target}
                  </span>
                  {rule.builtin && <Badge tone="slate">内置</Badge>}
                  <button
                    type="button"
                    disabled={rule.builtin || egressBusy}
                    onClick={() => void removeBlacklist(rule)}
                    className="min-h-11 px-2 text-rose-600 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    删除
                  </button>
                </li>
              ))
            )}
          </ul>
        </Card>

        <Card>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-ink">出站访问记录</h2>
              <p className="mt-1 text-xs text-ink-muted">
                查看沙箱访问目标与策略结果，直连 IP 需要重点核查。
              </p>
            </div>
            <Button
              size="sm"
              variant="secondary"
              loading={egressBusy}
              onClick={() => void loadEgress()}
            >
              刷新
            </Button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Badge tone="emerald">放行 {accessSummary.allow ?? 0}</Badge>
            <Badge tone="rose">拦截 {accessSummary.deny ?? 0}</Badge>
            <Badge tone="amber">ip-direct {accessSummary.ip_direct ?? 0}</Badge>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead>
                <tr className="border-b border-line text-left text-micro uppercase tracking-wide text-ink-muted">
                  <th className="px-2 py-2">目标</th>
                  <th className="px-2 py-2">端口</th>
                  <th className="px-2 py-2">动作</th>
                  <th className="px-2 py-2">流量</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line-subtle">
                {accesses.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-2 py-4 text-center text-ink-faint">
                      暂无出站访问记录。
                    </td>
                  </tr>
                ) : (
                  accesses.map((access, index) => (
                    <tr
                      key={`${access.occurred_at}-${index}`}
                      className={
                        access.target_domain == null ? 'bg-amber-50/60 dark:bg-amber-500/10' : ''
                      }
                    >
                      <td className="px-2 py-2 font-mono text-ink">
                        {access.target_domain ?? `ip-direct · ${access.target_ip}`}
                      </td>
                      <td className="px-2 py-2 tabular-nums text-ink-muted">{access.port}</td>
                      <td className="px-2 py-2">
                        <Badge tone={access.action === 'deny' ? 'rose' : 'emerald'}>
                          {access.action === 'deny' ? '拦截' : '放行'}
                        </Badge>
                      </td>
                      <td className="px-2 py-2 text-ink-muted">
                        {access.bytes_out == null ? '—' : formatBytes(access.bytes_out)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </Page>
  )
}
