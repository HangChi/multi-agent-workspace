import { useCallback, useEffect, useState } from 'react'

import {
  createAgentEgressRule,
  deleteAgentEgressRule,
  fetchAgentEgressRules,
  type EgressRule,
} from '../../api/endpoints'
import { extractError } from '../../api/client'
import { Badge } from '../ui/Badge'
import { Button } from '../ui/Button'
import { ErrorBanner } from '../ui/ErrorBanner'
import { Input } from '../ui/Field'
import { Skeleton } from '../ui/Skeleton'

const TARGET_TYPE_LABEL: Record<string, string> = {
  domain: '域名',
  ip: 'IP',
  cidr: '网段',
}

export function EgressRules({
  agentId,
  mode,
}: {
  agentId: string
  mode: 'default' | 'restricted'
}) {
  const [rules, setRules] = useState<EgressRule[]>([])
  const [loading, setLoading] = useState(true)
  const [target, setTarget] = useState('')
  const [port, setPort] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      // all: true —— 这是编辑表单，要列出并支持删除**全部**规则；
      // 分页会让靠后的规则既看不到也删不掉。
      const data = await fetchAgentEgressRules(agentId, { all: true })
      setRules(data.items)
    } catch (err) {
      setError(extractError(err))
    } finally {
      setLoading(false)
    }
  }, [agentId])

  useEffect(() => {
    void load()
  }, [load])

  const add = async () => {
    const value = target.trim().toLowerCase()
    if (!value) return
    setBusy(true)
    setError(null)
    try {
      await createAgentEgressRule(agentId, {
        target_type: value.includes('/')
          ? 'cidr'
          : value.includes(':') || /^(?:\d{1,3}\.){3}\d{1,3}$/.test(value)
            ? 'ip'
            : 'domain',
        target: value,
        port_range: port.trim() || null,
      })
      setTarget('')
      setPort('')
      await load()
    } catch (err) {
      setError(extractError(err))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (ruleId: string) => {
    setBusy(true)
    setError(null)
    try {
      await deleteAgentEgressRule(agentId, ruleId)
      await load()
    } catch (err) {
      setError(extractError(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-3 rounded-xl border border-line bg-surface-muted/50 p-4">
      <div>
        <h3 className="text-sm font-medium text-ink">Agent 出站允许清单</h3>
        <p className="mt-1 text-xs text-ink-muted">
          {mode === 'restricted'
            ? '限制模式下，仅允许清单内的域名、IP/网段和端口出网；全局黑名单仍不可绕过。'
            : '默认模式下规则仅用于预设；全局内网与云元数据黑名单不可绕过。'}
        </p>
        <p role="status" className="mt-1 text-xs text-amber-700 dark:text-amber-300">
          域名规则通过受控 DNS 按解析链和 TTL 绑定到 IP；不做 TLS 解密，共享 IP
          不等同于应用层域名身份校验。
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-[1fr_130px_auto]">
        <Input
          aria-label="域名或网段"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="例如 pypi.org 或 203.0.113.0/24"
          controlSize="sm"
        />
        <Input
          aria-label="端口范围"
          value={port}
          onChange={(e) => setPort(e.target.value)}
          placeholder="端口（可选）"
          controlSize="sm"
        />
        {/* size=md（h-10）与两个 sm 输入框同网格行对齐；sm 会矮一截 */}
        <Button size="md" variant="secondary" loading={busy} onClick={() => void add()}>
          添加规则
        </Button>
      </div>
      {error && <ErrorBanner error={error} variant="inline" className="text-xs" />}
      {loading ? (
        <div className="space-y-2" aria-hidden="true">
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
        </div>
      ) : rules.length > 0 ? (
        <ul className="divide-y divide-line rounded-lg border border-line bg-surface">
          {rules.map((rule) => (
            <li key={rule.id} className="flex items-center gap-3 px-3 py-2 text-xs">
              <Badge tone="slate">{TARGET_TYPE_LABEL[rule.target_type] ?? rule.target_type}</Badge>
              <span className="min-w-0 flex-1 truncate font-mono text-ink" title={rule.target}>
                {rule.target}
              </span>
              <span className="text-ink-faint">
                {rule.port_range ? `端口 ${rule.port_range}` : '全部端口'}
              </span>
              {/* ghost Button 自带焦点环；裸按钮此前键盘用户看不见焦点 */}
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => void remove(rule.id)}
                className="text-rose-600 hover:bg-rose-50 hover:text-rose-700 dark:text-rose-300 dark:hover:bg-rose-500/15"
              >
                删除
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-ink-faint">尚未配置允许规则。</p>
      )}
    </section>
  )
}
