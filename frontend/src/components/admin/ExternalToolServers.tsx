/**
 * Admin review controls for discovered external MCP tool versions.
 *
 * 细节：
 * - 凭证轮换从 window.prompt 改为 Modal + password Input：prompt 无法
 *   样式化、阻塞主线程、移动端体验差，且与全站弹层契约不一致；
 * - 撤销类操作（撤销凭证 / 撤销发布）走 useConfirm：二次点击即生效
 *   的破坏性操作是全站唯一没有确认的入口；
 * - 错误统一 extractError 解包后端 {error:{code,message}}；
 * - 查询三态：骨架 / 错误+重试 / 就绪；无服务器时给引导空态；
 * - 审核说明改用 Input 原语（focus ring 与全站一致）。
 */

import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import {
  approveExternalTool,
  fetchExternalServers,
  pauseMarketplaceTool,
  rejectExternalTool,
  resumeMarketplaceTool,
  revokeExternalCredential,
  revokeMarketplaceTool,
  rotateExternalCredential,
} from '../../api/endpoints'
import { extractError } from '../../api/client'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { EmptyState } from '../ui/EmptyState'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Icon } from '../ui/Icons'
import { Input } from '../ui/Field'
import { Modal } from '../ui/Modal'
import { Skeleton } from '../ui/Skeleton'
import { useConfirm } from '../ui/useConfirm'
import { useToast } from '../ui/useToast'

export function ExternalToolServers() {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const toast = useToast()
  const [reason, setReason] = useState('Reviewed source and data handling')
  const [error, setError] = useState<string | null>(null)
  /** 轮换凭证弹层：目标服务器 + 旧凭证信息 */
  const [rotating, setRotating] = useState<{
    serverId: string
    label: string
    kind: 'bearer' | 'api_key'
  } | null>(null)
  const [rotateValue, setRotateValue] = useState('')

  const {
    data,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['external-tool-servers'],
    queryFn: fetchExternalServers,
  })

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['external-tool-servers'] })

  const review = useMutation({
    mutationFn: ({
      serverId,
      versionId,
      approve,
    }: {
      serverId: string
      versionId: string
      approve: boolean
    }) =>
      approve
        ? approveExternalTool(serverId, versionId, { reason })
        : rejectExternalTool(serverId, versionId, { reason }),
    onSuccess: invalidate,
    onError: (cause) => setError(extractError(cause)),
  })
  const lifecycle = useMutation({
    mutationFn: ({
      listingId,
      action,
    }: {
      listingId: string
      action: 'pause' | 'resume' | 'revoke'
    }) => {
      if (action === 'pause') return pauseMarketplaceTool(listingId, { reason })
      if (action === 'resume') return resumeMarketplaceTool(listingId, { reason })
      return revokeMarketplaceTool(listingId, { reason })
    },
    onSuccess: invalidate,
    onError: (cause) => setError(extractError(cause)),
  })
  const credentialMutation = useMutation({
    mutationFn: ({ serverId, credentialId }: { serverId: string; credentialId: string }) =>
      revokeExternalCredential(serverId, credentialId),
    onSuccess: invalidate,
    onError: (cause) => setError(extractError(cause)),
  })
  const rotateMutation = useMutation({
    mutationFn: ({ serverId, label, kind, value }: { serverId: string; label: string; kind: 'bearer' | 'api_key'; value: string }) =>
      rotateExternalCredential(serverId, { label, kind, value }),
    onSuccess: () => {
      setRotating(null)
      setRotateValue('')
      toast.success('凭证已轮换', '旧凭证已失效，新凭证不会回显')
      invalidate()
    },
    onError: (cause) => setError(extractError(cause)),
  })

  const revokeCredential = async (serverId: string, credentialId: string, label: string) => {
    const ok = await confirm({
      title: `撤销凭证「${label}」？`,
      description: '撤销后使用该凭证的工具调用会立即失败，需要轮换新凭证才能恢复。',
      confirmText: '撤销',
      tone: 'danger',
    })
    if (ok) credentialMutation.mutate({ serverId, credentialId })
  }

  const revokeListing = async (listingId: string, name: string) => {
    const ok = await confirm({
      title: `撤销发布「${name}」？`,
      description: '撤销后该工具从市场下架，已安装的用户会失去访问。',
      confirmText: '撤销发布',
      tone: 'danger',
    })
    if (ok) lifecycle.mutate({ listingId, action: 'revoke' })
  }

  if (isLoading)
    return (
      <Card>
        <div className="space-y-3" aria-hidden="true">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      </Card>
    )

  const servers = data?.items ?? []

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-ink">外部工具审核</h2>
          <p className="mt-1 text-xs text-ink-muted">仅发布验证通过的工具版本。凭证值不会显示。</p>
        </div>
        <Badge tone="slate">{data?.total ?? 0} 个服务器</Badge>
      </div>
      {isError && <ErrorBanner error="外部工具服务器加载失败" retry={() => void refetch()} />}
      {error && <ErrorBanner error={error} onDismiss={() => setError(null)} className="mt-3" />}
      <div className="mt-4 max-w-md">
        <Input
          label="审核说明"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          hint="发布 / 拒绝 / 暂停等操作会带上这条说明进入审计日志"
          maxLength={200}
        />
      </div>

      {!isError && servers.length === 0 ? (
        <EmptyState
          className="mt-4"
          icon="plug"
          title="还没有发现外部工具服务器"
          description="工具服务端注册后会出现在这里，验证通过即可发布到工具市场。"
        />
      ) : (
        <div className="mt-4 divide-y divide-line-subtle">
          {servers
            .flatMap((server) => server.tools.map((tool) => ({ server, tool })))
            .map(({ server, tool }) => (
              <div key={tool.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">
                    {server.name} / {tool.external_name}
                  </p>
                  <p className="truncate text-xs text-ink-muted">
                    v{tool.version} · {tool.description}
                  </p>
                </div>
                <Badge tone={server.current_validation_status === 'passed' ? 'emerald' : 'amber'}>
                  {server.current_validation_status ?? '待验证'}
                </Badge>
                {server.credentials?.map((credential) => (
                  <div key={credential.id} className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                    <span>
                      {credential.label} · {credential.status}
                    </span>
                    {credential.status === 'active' && (
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          tone="danger"
                          disabled={credentialMutation.isPending}
                          onClick={() =>
                            void revokeCredential(server.id, credential.id, credential.label)
                          }
                        >
                          撤销凭证
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={rotateMutation.isPending}
                          onClick={() => {
                            setRotating({
                              serverId: server.id,
                              label: `${credential.label}-rotated`,
                              kind: credential.kind as 'bearer' | 'api_key',
                            })
                            setRotateValue('')
                          }}
                        >
                          轮换凭证
                        </Button>
                      </>
                    )}
                  </div>
                ))}
                <div className="flex flex-wrap gap-2">
                  {tool.listing_status === 'published' && tool.listing_id ? (
                    <>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={lifecycle.isPending}
                        onClick={() =>
                          lifecycle.mutate({ listingId: tool.listing_id!, action: 'pause' })
                        }
                      >
                        暂停
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        tone="danger"
                        disabled={lifecycle.isPending}
                        onClick={() => void revokeListing(tool.listing_id!, tool.external_name)}
                      >
                        撤销
                      </Button>
                    </>
                  ) : tool.listing_status === 'paused' && tool.listing_id ? (
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={lifecycle.isPending}
                      onClick={() =>
                        lifecycle.mutate({ listingId: tool.listing_id!, action: 'resume' })
                      }
                    >
                      恢复
                    </Button>
                  ) : (
                    <>
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={review.isPending || server.current_validation_status !== 'passed'}
                        onClick={() =>
                          review.mutate({ serverId: server.id, versionId: tool.id, approve: true })
                        }
                      >
                        发布
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={review.isPending}
                        onClick={() =>
                          review.mutate({ serverId: server.id, versionId: tool.id, approve: false })
                        }
                      >
                        拒绝
                      </Button>
                    </>
                  )}
                </div>
              </div>
            ))}
        </div>
      )}

      {/* 轮换凭证：password 输入 + 不自动填充 + 提交后清空。
          window.prompt 版本无法样式化、阻塞主线程、移动端不可用。 */}
      <Modal
        open={!!rotating}
        onClose={() => {
          setRotating(null)
          setRotateValue('')
        }}
        title="轮换凭证"
        description="提交后立即生效，旧凭证同时失效。凭证值不会回显，也不会写入审计日志。"
        icon={
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-50 text-brand-600 ring-1 ring-inset ring-brand-100">
            <Icon name="key" className="h-4 w-4" />
          </span>
        }
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setRotating(null)
                setRotateValue('')
              }}
            >
              取消
            </Button>
            <Button
              variant="primary"
              loading={rotateMutation.isPending}
              disabled={!rotateValue.trim()}
              onClick={() => {
                if (!rotating || !rotateValue.trim()) return
                rotateMutation.mutate({
                  serverId: rotating.serverId,
                  label: rotating.label,
                  kind: rotating.kind,
                  value: rotateValue.trim(),
                })
              }}
            >
              确认轮换
            </Button>
          </>
        }
      >
        <Input
          label="新凭证值"
          type="password"
          value={rotateValue}
          onChange={(e) => setRotateValue(e.target.value)}
          autoComplete="new-password"
          placeholder="仅本次提交，不会保存到浏览器"
          required
        />
      </Modal>
    </Card>
  )
}
